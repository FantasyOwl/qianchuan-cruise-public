"""Offline import of explicitly identified official account report responses.

No network, credentials or platform access. Never reconstruct a payment ROI
numerator by multiplying a rounded ROI or by summing differently defined GMV.
"""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re

from .official_fields import ENDPOINT,FORMAT,FIELDS,REFERENCE,LIMITS
from .schema import normalize_snapshot,parse_time,now_cn,numeric

FORBIDDEN={'token','accesstoken','refreshtoken','authorization','cookie','setcookie','headers','requestheaders',
           'secret','appsecret','clientsecret','password','authcode','apikey','csrf','csrftoken','localstorage','sessionstorage','storage'}


def reject_duplicate_keys(pairs):
    value={}
    for key,item in pairs:
        if key in value:raise ValueError('JSON含重复字段，不能选择其中一个查询或指标值')
        value[key]=item
    return value


def _document(payload):
    if isinstance(payload,dict) and 'document_text' in payload:
        if set(payload)-{'document_text','preview_digest'} or not isinstance(payload['document_text'],str):
            raise ValueError('原始报告文件请求格式无效')
        try:
            value=json.loads(payload['document_text'],object_pairs_hook=reject_duplicate_keys,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError('报告不允许非有限数字')))
        except (json.JSONDecodeError,RecursionError):raise ValueError('报告文件不是可解析的JSON对象') from None
        if not isinstance(value,dict) or 'preview_digest' in value:raise ValueError('原始报告文件不能带预览摘要')
        if 'preview_digest' in payload:value['preview_digest']=payload['preview_digest']
        return value
    return payload


def _guard(value,depth=0):
    if depth>20:raise ValueError('报告嵌套过深')
    if isinstance(value,dict):
        for key,item in value.items():
            if not isinstance(key,str):raise ValueError('报告JSON字段名无效')
            clean=re.sub(r'[^a-z0-9]','',key.lower())
            if clean in FORBIDDEN or any(clean.startswith(k) for k in ('accesstoken','refreshtoken','clientsecret','appsecret')):
                raise ValueError('只接收无凭据的查询参数和业务响应，请移除请求头、令牌及登录资料')
            _guard(item,depth+1)
    elif isinstance(value,list):
        for item in value:_guard(item,depth+1)


def _value(value,count=False):
    if isinstance(value,str):
        if not re.fullmatch(r'\d+(?:\.\d+)?',value) or len(value)>80:raise ValueError('官方数值必须为非负数字或普通十进制数字字符串')
        try:value=float(Decimal(value))
        except (InvalidOperation,ValueError,OverflowError):raise ValueError('官方数字格式无效') from None
    value=numeric(value)
    if value is not None and count:
        if value!=int(value):raise ValueError('官方订单计数必须为整数')
        value=int(value)
    return value


def _query(raw,settings):
    allowed={'advertiser_id','start_time','end_time','adlab_scene','data_period','marketing_goal','order_platform','fields'}
    if not isinstance(raw,dict) or set(raw)!=allowed:raise ValueError('请提供完整且无附加字段的账户报表查询参数')
    account_id=raw['advertiser_id']
    if type(account_id) is int:account_id=str(account_id)
    accounts={a['id']:a for a in settings.get('accounts',[])}
    if not isinstance(account_id,str) or account_id not in accounts:raise ValueError('报表账户不在示例分组A白名单内')
    enums={'adlab_scene':{'OVERALL_PROJECT','UNI_PROJECT'},'data_period':{'ALL_DATA','OVER_ALL_DATA','UNI_DATA'},
           'marketing_goal':{'ALL','LIVE_PROM_GOODS','VIDEO_PROM_GOODS'},'order_platform':{'ALL','QIANCHUAN','ECP_AWEME'}}
    for field,values in enums.items():
        if not isinstance(raw[field],str) or raw[field] not in values:raise ValueError('报表计划类型、期间、营销目标或下单平台不明确')
    expected='LIVE_PROM_GOODS' if accounts[account_id]['scene']=='live' else 'VIDEO_PROM_GOODS'
    if raw['marketing_goal'] not in {'ALL',expected}:raise ValueError('查询营销目标与该账户场景不一致')
    start,end=parse_time(raw['start_time']),parse_time(raw['end_time'])
    if end<start:raise ValueError('查询结束时间早于开始时间')
    fields=raw['fields']
    if not isinstance(fields,list) or not fields or any(not isinstance(k,str) or k not in FIELDS for k in fields) or len(set(fields))!=len(fields):
        raise ValueError('查询字段缺失、重复或尚未核验，不能自动猜测映射')
    return {**raw,'advertiser_id':account_id,'start_time':start.isoformat(),'end_time':end.isoformat(),'fields':sorted(fields)},start,end


def official_evidence(snapshot):
    ctx=snapshot.get('context',{})
    if not isinstance(ctx,dict) or ctx.get('report_endpoint')!=ENDPOINT:return None
    return {'endpoint':ENDPOINT,'reference':REFERENCE,'query':{k:ctx.get('report_'+k) for k in
        ('adlab_scene','data_period','marketing_goal','order_platform','fields')},
        'start_time':ctx.get('report_start_at'),'end_time':ctx.get('report_end_at'),
        'account_id':snapshot.get('account_id'),'data_at':snapshot.get('source',{}).get('data_at'),
        'captured_at':snapshot.get('source',{}).get('captured_at'),
        'values':[{'source_field':field,'metric':FIELDS[field][0],'label':FIELDS[field][1],'unit':FIELDS[field][2],
                   'value':snapshot.get('metrics',{}).get(FIELDS[field][0])} for field in ctx.get('report_fields',[]) if field in FIELDS],
        'limits':list(LIMITS),'platform_verified':False}


def convert_official_reports(payload,settings,received_at=None):
    """Return normalized snapshots and preview; does not persist anything."""
    received_at=received_at or now_cn();payload=_document(payload);_guard(payload)
    if not isinstance(payload,dict) or set(payload)-{'format','reports','preview_digest'} or payload.get('format')!=FORMAT:
        raise ValueError('请使用官方账户报表导入模板，不接收任意请求或授权响应')
    reports=payload.get('reports')
    if not isinstance(reports,list) or not 1<=len(reports)<=1000:raise ValueError('每次需要1至1000条独立报表结果')
    snapshots=[]
    for frame in reports:
        if not isinstance(frame,dict) or set(frame)-{'endpoint','query','response','captured_at','data_at'} or frame.get('endpoint')!=ENDPOINT:
            raise ValueError('报告接口未支持；当前只支持账户聚合报表all_promotion/get')
        query,start,end=_query(frame.get('query'),settings)
        captured=parse_time(frame.get('captured_at'))
        if start>captured:raise ValueError('查询开始时间晚于本次采集时间')
        data_at=parse_time(frame['data_at']) if frame.get('data_at') is not None else None
        if data_at and not start<=data_at<=end:raise ValueError('数据更新时间不在该查询时间范围内')
        response=frame.get('response')
        if not isinstance(response,dict) or type(response.get('code')) is not int or response['code']!=0:
            raise ValueError('官方业务响应不是明确成功，不导入错误信息或授权数据')
        data=response.get('data')
        if not isinstance(data,dict):raise ValueError('响应data必须为账户聚合指标对象')
        if 'advertiser_id' in data and (type(data['advertiser_id']) not in (str,int) or str(data['advertiser_id'])!=query['advertiser_id']):
            raise ValueError('响应账户标识与查询不一致')
        metrics={}
        for field in query['fields']:
            if field in data:
                metric,label,unit=FIELDS[field];metrics[metric]=_value(data[field],unit=='单')
        if not any(v is not None for v in metrics.values()):raise ValueError('没有请求字段对应的可识别业务指标，不能将空响应填零')
        # End-time changes are repeated cumulative observations only when all
        # other query dimensions, requested fields and start boundary match.
        identity={k:v for k,v in query.items() if k!='end_time'}
        dataset='official-account-'+hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()[:30]
        daily=start==start.replace(hour=0,minute=0,second=0,microsecond=0) and end.date()==start.date()
        context={'report_endpoint':ENDPOINT,'report_start_at':start.isoformat(),'report_end_at':end.isoformat(),
                 **{'report_'+k:query[k] for k in ('adlab_scene','data_period','marketing_goal','order_platform','fields')}}
        row={'account_id':query['advertiser_id'],'entity_id':query['advertiser_id'],'scope':'account','metric_basis':'overall_payment',
             'promotion_type':'chengfang' if query['adlab_scene']=='OVERALL_PROJECT' else 'universal',
             'source':{'kind':'import','dataset_id':dataset,'captured_at':captured.isoformat(),
                'data_at':data_at.isoformat() if data_at else None,'metric_date':start.date().isoformat(),
                'metric_period':'day' if daily else 'unknown','period_start':None if daily else start.isoformat(),'coverage':'visible_only'},
             'metrics':metrics,'context':context,
             'quality':{'issues':['官方聚合报告仅覆盖明确查询范围，未核验账户全量与ROI金额分子']},
             'control':{'history_known':False,'guarantee_verified':False}}
        snapshots.append(normalize_snapshot(row,settings,received_at))
    unique={}
    for snapshot in snapshots:
        identity=(snapshot['account_id'],snapshot['source']['series_key'],snapshot['source']['data_at'] or snapshot['source']['captured_at'])
        if identity in unique and unique[identity]['metrics']!=snapshot['metrics']:raise ValueError('同一次查询观测存在冲突业务指标，整批未导入')
        unique[identity]=snapshot
    snapshots=list({s['id']:s for s in snapshots}.values())
    digest=hashlib.sha256(json.dumps(snapshots,ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
    return {'snapshots':snapshots,'preview_digest':digest,'preview':[official_evidence(s) for s in snapshots],
            'received_reports':len(reports),'normalized_snapshots':len(snapshots),'executed':False,'platform_verified':False}
