"""Native configuration/list comparison and a disconnected default-off reader.

Material lists contain spend-filtered observations, never a complete inventory.
Neither a supplied file nor this reader resolves a write or releases a hold.
"""
import copy
import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date,datetime,timedelta

from .creative_rotation import _exact,_text,_id as input_id
from .official_reports import _document,_guard
from .plan_detail import prepare_detail_request,_NoRedirect,DETAIL_PATH,DETAIL_SOURCE
from .plan_readback import _business_data,_id,_clock
from .schema import CN,now_cn

FORMAT='qianchuan_material_readback_v1'
PATH='/open_api/v1.0/qianchuan/uni_promotion/ad/material/get/'
SOURCE='https://open.oceanengine.com/labels/12/docs/1804363488115850'
KINDS={'VIDEO':'video_material','IMAGE':'image_material','TITLE':'title_material','CAROUSEL':'carousel_material','LIVE_ROOM':'room_material'}
STATUSES={'DELIVERY_OK','DELIVERY_NOT','DELETED','EXCLUDE'}
AUDITS={'PASS','REJECT','IN_PROGRESS'}


def _date(value):
    try:
        if not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value:raise ValueError
        return date.fromisoformat(value)
    except ValueError:raise ValueError('素材查询日期须为明确的YYYY-MM-DD') from None


def prepare_material_request(query,settings):
    _exact(query,{'account_id','plan_id','material_type','start_date','end_date','page_size','max_pages'})
    aid,pid=input_id(query['account_id']),input_id(query['plan_id']);kind=query['material_type']
    detail=prepare_detail_request(aid,pid,settings)
    if not isinstance(kind,str) or kind not in KINDS:raise ValueError('素材读取类型无效')
    start,end=map(_date,(query['start_date'],query['end_date']))
    if not 0<=(end-start).days<31:raise ValueError('本地素材读取窗口为1至31个自然日')
    if type(query['page_size']) is not int or query['page_size'] not in {10,20,50,100}:raise ValueError('素材分页大小无效')
    if type(query['max_pages']) is not int or not 1<=query['max_pages']<=20:raise ValueError('本地素材页数上限为1至20')
    filtering={'material_type':kind,'start_date':query['start_date'],'end_date':query['end_date']}
    # Explicit ALL where documented; never send unsupported filters or the retired video_type.
    if kind in {'VIDEO','TITLE','CAROUSEL'}:filtering['material_status']='ALL'
    scene='live' if detail['marketing_goal']=='LIVE_PROM_GOODS' else 'product'
    if kind=='VIDEO' or scene=='product' and kind in {'TITLE','IMAGE','CAROUSEL'}:filtering['material_select_type']='ALL'
    params={'advertiser_id':aid,'ad_id':pid,'filtering':json.dumps(filtering,separators=(',',':')),
        'fields':'["stat_cost_for_roi2"]','order_field':'stat_cost_for_roi2','order_type':'DESC','page_size':query['page_size']}
    identity={'query':query,'marketing_goal':detail['marketing_goal'],'detail_url':detail['url'],'params':params}
    return {'query':copy.deepcopy(query),'account_id':aid,'plan_id':pid,'material_type':kind,'scene':scene,
        'marketing_goal':detail['marketing_goal'],'params':params,'detail_url':detail['url'],
        'digest':hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'read_only':True,'network_enabled':False,'references':[DETAIL_SOURCE,SOURCE]}


def _target(raw,request):
    _exact(raw,{'native','desired_presence'},{'material_id','product_id','aweme_uid'})
    if not isinstance(raw['desired_presence'],str) or raw['desired_presence'] not in {'present','absent'}:raise ValueError('核对须明确配置存在或缺失目标')
    native=raw['native'];kind=request['material_type']
    if kind=='VIDEO':
        _exact(native,set(),{'video_id','aweme_item_id'})
        if len(native)!=1:raise ValueError('视频核对须明确素材库或抖音视频ID')
        if 'video_id' in native:_text(native['video_id'])
        else:input_id(native['aweme_item_id'])
    elif kind=='IMAGE':
        _exact(native,{'image_ids'});_identity(kind,native)
        for v in native['image_ids']:_text(v)
    elif kind=='TITLE':
        _exact(native,{'title'},{'title_type'} if request['scene']=='product' else ())
        _text(native['title'])
        if request['scene']=='product' and (not isinstance(native.get('title_type'),str) or native['title_type'] not in {'CUSTOM','COMMODITY_CARD'}):
            raise ValueError('商品静态标题须明确类型')
    elif kind=='CAROUSEL':
        _exact(native,set(),{'carousel_id','aweme_carousel_id'})
        if len(native)!=1:raise ValueError('图文核对须明确素材库或抖音图文ID')
        input_id(next(iter(native.values())))
    else:_exact(native,set())
    out={**raw,'native':copy.deepcopy(native)}
    for k in ('material_id','product_id','aweme_uid'):
        if k in out:input_id(out[k])
    if request['scene']=='product':input_id(out.get('product_id'))
    elif 'product_id' in out:raise ValueError('直播素材不按商品用途查询')
    if request['material_type']=='LIVE_ROOM':input_id(out.get('material_id'))
    return out


def _identity(kind,item):
    if not isinstance(item,dict):raise ValueError('原生素材配置行无效')
    if kind=='VIDEO':
        aweme=item.get('aweme_item_id')
        if aweme is not None and (type(aweme) is bool or aweme not in (0,'0')):
            return kind,'aweme_item_id',_id(aweme)
        video=item.get('video_id')
        if not isinstance(video,str) or not video or len(video)>500:raise ValueError('配置视频缺少可识别原生ID')
        return kind,'video_id',video
    if kind=='CAROUSEL':
        if any(type(item.get(key)) is bool for key in ('carousel_id','aweme_carousel_id')):raise ValueError('配置图文ID不能为布尔值')
        ids=[(key,_id(item[key])) for key in ('carousel_id','aweme_carousel_id') if key in item and item[key] not in (None,0,'0')]
        if len(ids)!=1:raise ValueError('配置图文的原生标识缺失或冲突')
        return kind,*ids[0]
    if kind=='IMAGE':
        values=item.get('image_ids')
        if not isinstance(values,list) or not 1<=len(values)<=9 or any(not isinstance(v,str) or not v or len(v)>500 for v in values) or len(set(values))!=len(values):
            raise ValueError('配置图片组标识无效')
        return kind,tuple(values)
    if kind=='TITLE':
        value=item.get('title')
        if not isinstance(value,str) or not value or len(value)>500:raise ValueError('配置标题无效')
        return kind,value
    raise ValueError('详情没有已核验的直播画面素材ID映射')


def _raw(value):
    if isinstance(value,str):return value
    if not isinstance(value,dict):raise ValueError('请提供原始业务响应对象或JSON文本')
    try:return json.dumps(value,ensure_ascii=False,allow_nan=False)
    except (ValueError,TypeError):raise ValueError('业务响应对象不能包含非有限数字') from None


def _configuration(raw,request,target):
    data=_business_data(_raw(raw));kind=request['material_type'];notes=[]
    if _id(data.get('ad_id'))!=request['plan_id'] or data.get('marketing_goal')!=request['marketing_goal']:
        raise ValueError('计划详情ID或营销目标与所选账户不符')
    result={'state':'unavailable','matches':0,'scope_explicit':False,'notes':notes,
        'modify_time_raw':data.get('modify_time') if isinstance(data.get('modify_time'),str) else None,
        'platform_state_at':None,'creative_mode':None}
    if kind=='LIVE_ROOM':notes.append('详情没有已核验的直播画面素材ID配置映射');return result
    lists=[];field=KINDS[kind]
    if request['scene']=='live':
        if kind not in {'VIDEO','TITLE'}:notes.append('直播详情不包含该素材配置族');return result
        container=data.get('programmatic_creative_media_list')
        if not isinstance(container,dict) or field not in container:notes.append('详情未明确返回对应配置数组，不能当作空数组');return result
        lists=[container[field]]
        # A structure named programmatic does not certify a live creative mode.
    else:
        products=data.get('multi_product_creative_list')
        if not isinstance(products,list) or len(products)>1000:notes.append('详情缺少有界商品创意配置数组');return result
        multi=data.get('is_multi_aweme_uid')
        if type(multi) is not bool:notes.append('多号范围未明确，不能推断用途');return result
        if multi and not target.get('aweme_uid'):notes.append('多号素材须明确抖音UID');return result
        uses=set()
        for p in products:
            if not isinstance(p,dict):raise ValueError('商品创意配置结构无效')
            product=_id(p.get('product_id'));uid=_id(p['aweme_uid']) if p.get('aweme_uid') is not None else None
            if multi and uid is None:raise ValueError('多号配置缺少抖音UID')
            if (product,uid) in uses:raise ValueError('详情出现重复商品/抖音用途，不能选择有利配置')
            uses.add((product,uid))
            if product!=target['product_id']:continue
            if target.get('aweme_uid') is not None and uid!=target['aweme_uid']:continue
            if field not in p:notes.append('目标用途没有明确返回对应素材数组');return result
            if p.get('creative_type')!='PROGRAMMATIC_CREATIVE':notes.append('商品创意类型未明确为程序化');return result
            result['creative_mode']='PROGRAMMATIC_CREATIVE';lists.append(p[field])
        if not multi and len({uid for _,uid in uses if uid is not None})>1:raise ValueError('单号声明与多个抖音UID配置矛盾')
    result['scope_explicit']=True;desired=_identity(kind,target['native']);matches=0;ambiguous=False
    for items in lists:
        if not isinstance(items,list) or len(items)>1000:raise ValueError('素材配置数组结构无效或超出本地限制')
        for item in items:
            identity=_identity(kind,item)
            if identity==desired:
                if kind=='TITLE' and (item.get('dynamic_words') or target['native'].get('title_type') is not None and item.get('title_type')!=target['native']['title_type']):ambiguous=True
                else:matches+=1
            if kind=='IMAGE' and set(identity[1])&set(desired[1]) and identity!=desired:ambiguous=True
    result.update(matches=matches,state='ambiguous' if ambiguous or matches>1 else 'present' if matches==1 else 'not_observed')
    if ambiguous:notes.append('标题类型/动态词或图片组范围冲突，不按同文/单图猜测同一配置')
    return result


def _page(raw,page,size):
    data=_business_data(_raw(raw));rows=data.get('ad_material_infos');info=data.get('page_info')
    if not isinstance(rows,list) or not isinstance(info,dict):raise ValueError('素材列表或分页字段缺失')
    values=[info.get(k) for k in ('page','page_size','total_number','total_page')]
    if any(type(v) is not int for v in values):raise ValueError('素材分页须为明确整数')
    p,s,total,pages=values
    if p!=page or s!=size or total<0 or pages not in ({0,1} if total==0 else {(total+size-1)//size}) or p>max(1,pages) or len(rows)!=min(size,max(0,total-(page-1)*size)):
        raise ValueError('素材分页、总数与行数不一致')
    return rows,total,pages


def _row(raw,kind):
    if not isinstance(raw,dict) or not isinstance(raw.get('material_info'),dict):raise ValueError('素材信息结构无效')
    info=raw['material_info']
    if info.get('material_type')!=kind or not isinstance(info.get(KINDS[kind]),dict):raise ValueError('返回素材类型与请求不符')
    item=info[KINDS[kind]];mid=_id(item.get('material_id'))
    if kind=='IMAGE':identity=_identity(kind,{'image_ids':[item.get('image_id')]})
    elif kind in {'CAROUSEL','LIVE_ROOM'}:identity=None
    else:identity=_identity(kind,item)
    products=raw.get('product_info',[])
    if not isinstance(products,list) or any(not isinstance(p,dict) for p in products):raise ValueError('素材关联商品结构无效')
    products=[_id(p.get('product_id')) for p in products]
    if len(set(products))!=len(products):raise ValueError('素材关联商品重复')
    if 'is_delete' in raw and type(raw['is_delete']) is not bool:raise ValueError('素材删除标记须为布尔值')
    status=raw.get('material_status');audit=raw.get('audit_status');select=raw.get('material_select_type')
    if any(v is not None and (not isinstance(v,str) or v not in allowed) for v,allowed in ((status,STATUSES),(audit,AUDITS),(select,{'CUSTOM','AUTO'}))):
        raise ValueError('素材状态或来源枚举未知，不能推断执行状态')
    return {'material_id':mid,'identity':identity,'product_ids':products,'material_status':status,
        'audit_status':audit,'is_delete':raw.get('is_delete'),'material_select_type':select,
        'aggregate_usable_observed':True if status=='DELIVERY_OK' and audit=='PASS' and raw.get('is_delete') is False else None,
        'reported_deleted':True if status=='DELETED' and raw.get('is_delete') is True else None,
        'deletion_eligibility_verified':False}


def review_supplied_materials(payload,settings,now=None):
    now=_clock(now or now_cn())
    if isinstance(payload,dict) and 'document_text' in payload and (not isinstance(payload['document_text'],str) or len(payload['document_text'].encode())>2*1024*1024):
        raise ValueError('素材核对文件不能超过2MB')
    payload=_document(payload);_guard(payload)
    _exact(payload,{'format','query','target','request_started_at','received_at','detail_response','material_responses'})
    if payload['format']!=FORMAT:raise ValueError('素材核对格式不符')
    request=prepare_material_request(payload['query'],settings);target=_target(payload['target'],request)
    started,received=map(_clock,(payload['request_started_at'],payload['received_at']))
    if not started<=received<=now or (now-received).total_seconds()>600 or (received-started).total_seconds()>600 or _date(request['query']['end_date'])>started.astimezone(CN).date():
        raise ValueError('素材读取时序、日期或时效不符')
    config=_configuration(payload['detail_response'],request,target);pages=payload['material_responses']
    if not isinstance(pages,list) or not 1<=len(pages)<=request['query']['max_pages']:raise ValueError('须提供从第一页开始的有界素材响应')
    known={};total=page_count=None
    for page,raw in enumerate(pages,1):
        rows,n,p=_page(raw,page,request['query']['page_size'])
        if total is not None and (n,p)!=(total,page_count):raise ValueError('素材读取期间分页总数变化')
        total,page_count=n,p
        for raw in rows:
            row=_row(raw,request['material_type']);mid=row['material_id']
            if mid in known:raise ValueError('素材跨页重复，不能选择有利状态')
            known[mid]=row
    kind=request['material_type'];desired=None if kind=='LIVE_ROOM' else _identity(kind,target['native']);matches=[]
    for row in known.values():
        if target.get('material_id') is not None and row['material_id']!=target['material_id']:continue
        if target.get('material_id') is None and kind in {'CAROUSEL','LIVE_ROOM'}:continue
        if kind not in {'CAROUSEL','LIVE_ROOM'} and row['identity']!=desired:
            if target.get('material_id') is not None:raise ValueError('声明素材ID与返回的原生标识映射矛盾')
            continue
        if request['scene']=='product' and target['product_id'] not in row['product_ids']:continue
        matches.append({**row,'native_identity_matches':None if kind in {'CAROUSEL','LIVE_ROOM'} else True})
    return {'format':FORMAT,'checked_at':now.isoformat(),'account_id':request['account_id'],'plan_id':request['plan_id'],
        'material_type':kind,'target':target,'query':request['query'],'configuration':config,'comparison_scope':'native_identity_membership_only',
        'desired_state_matches_returned_configuration':config['state']==('present' if target['desired_presence']=='present' else 'not_observed') and config['scope_explicit'],
        'material_observations':matches,'received_pages':len(pages),'reported_pages':page_count,'reported_rows':total,
        'pagination_exhausted':len(pages)>=max(1,page_count),'matched_observations':len(matches),
        'complete_inventory_verified':False,'use_specific_usable_verified':False,'platform_verified':False,
        'can_release_hold':False,'submission_state_changed':False,'executed':False,'persisted':False,'ai_sent':False,
        'references':[DETAIL_SOURCE,SOURCE],
        'limits':['素材列表仅覆盖查询日期有消耗的素材；读到末页也不证明完整配置或已删除。',
            '只核对原生标识或静态标题的配置成员关系，不比较画幅、封面等完整设置。',
            '图文与直播画面的列表素材ID仅按文件声明关联，不认证素材库/抖音原生标识映射。',
            '详情匹配只描述返回的明确配置；省略数组不按空数组处理，修改时间原文不认证平台状态时钟。',
            '列表状态为素材聚合观察，商品关联不认证具体商品/抖音用途的可用状态。CUSTOM不是自提删除资格。',
            '文件和传输不认证执行归因，不解除流程占用、不重试、不生成观察快照或调控资格。']}


@dataclass(frozen=True)
class MaterialReadPermit:
    account_id:str
    request_digest:str
    issued_at:datetime
    expires_at:datetime
    authorization_reference:str


def _get(kind,params,token,timeout):
    path=DETAIL_PATH if kind=='detail' else PATH if kind=='materials' else None
    if path is None:raise ValueError('素材读取路径无效')
    url='https://api.oceanengine.com'+path+'?'+urllib.parse.urlencode(params)
    request=urllib.request.Request(url,headers={'Access-Token':token,'Cache-Control':'no-cache'},method='GET')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    with opener.open(request,timeout=timeout) as response:
        if response.geturl()!=url:raise ValueError('素材读取响应地址发生变化')
        raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('素材读取单页超过1MiB')
        return raw.decode('utf-8')


class MaterialReadClient:
    """Disconnected GET-only integration boundary; serving never enables it."""
    def __init__(self,*,enable_network=False,transport=None,clock=None):
        if transport is not None and not callable(transport):raise ValueError('素材传输器须为显式可调用对象')
        if clock is not None and not callable(clock):raise ValueError('素材读取时钟须为可调用对象')
        self.enabled=enable_network is True;self.transport=_get if transport is None else transport
        self.custom_transport=transport is not None;self.clock=now_cn if clock is None else clock

    def read(self,query,target,settings,permit,token_provider):
        if not self.enabled:raise PermissionError('素材读取适配器默认关闭，观察服务没有原生读取入口')
        request=prepare_material_request(query,settings);target=_target(target,request);began=_clock(self.clock())
        if (not isinstance(permit,MaterialReadPermit) or not isinstance(permit.issued_at,datetime)
            or not isinstance(permit.expires_at,datetime) or permit.issued_at.tzinfo is None or permit.expires_at.tzinfo is None
            or not permit.issued_at<=began<permit.expires_at or not timedelta(0)<permit.expires_at-permit.issued_at<=timedelta(minutes=5)
            or permit.account_id!=request['account_id'] or permit.request_digest!=request['digest']
            or not isinstance(permit.authorization_reference,str) or not permit.authorization_reference.strip()):
            raise PermissionError('需要该账户和具体查询的有效读取授权记录')
        if _date(query['end_date'])>began.astimezone(CN).date():raise ValueError('素材查询不能包含未来日期')
        try:token=token_provider()
        except Exception:raise ValueError('显式凭据提供器未返回可用凭据') from None
        if not isinstance(token,str) or not 1<=len(token)<=4096 or any(ord(c)<33 or ord(c)>126 for c in token):
            raise ValueError('开放平台凭据格式无效')
        started=_clock(self.clock())
        if not began<=started<permit.expires_at:raise PermissionError('读取授权已过期或时钟倒退')
        # A provider cannot change the query/account scope while supplying a token.
        fresh=prepare_material_request(query,settings)
        if fresh['digest']!=request['digest']:raise PermissionError('读取范围在请求前发生变化')
        query=request['query']
        pages=[];last_clock=started;total_bytes=0
        try:
            detail=_raw(self.transport('detail',{'advertiser_id':request['account_id'],'ad_id':request['plan_id']},token,10))
            total_bytes+=len(detail.encode());_configuration(detail,request,target)
            for page in range(1,query['max_pages']+1):
                at=_clock(self.clock())
                if not last_clock<=at<permit.expires_at:raise ValueError('读取时钟或授权过期')
                last_clock=at
                raw=_raw(self.transport('materials',{**request['params'],'page':page},token,10))
                total_bytes+=len(raw.encode())
                if total_bytes>2*1024*1024:raise ValueError('素材核对累计响应超过2MiB')
                _,_,count=_page(raw,page,query['page_size']);pages.append(raw)
                if page>=max(1,count):break
            received=_clock(self.clock())
            if not last_clock<=received<permit.expires_at:raise ValueError('读取结束时钟或授权无效')
            package={'format':FORMAT,'query':request['query'],'target':target,'request_started_at':started.isoformat(),
                'received_at':received.isoformat(),'detail_response':detail,'material_responses':pages}
            comparison=review_supplied_materials(package,settings,received)
        except Exception:
            return {'ok':False,'read_attempted':True,'read_only':True,'automatic_retry':False,
                'persisted':False,'submission_state_changed':False,'can_release_hold':False,
                'note':'素材读取或核对未完成；不保留原始异常，不改变提交或流程占用'}
        return {'ok':True,'read_attempted':True,'read_only':True,'automatic_retry':False,'persisted':False,
            'transport_kind':'custom_transport' if self.custom_transport else 'official_https','comparison':comparison,
            'submission_state_changed':False,'can_release_hold':False,'platform_verified':False}
