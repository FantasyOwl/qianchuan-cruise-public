"""Fixed native control IO receipts. No credentials or IO on import.

Private receipts cannot be supplied as JSON. Origin qualification describes
this HTTPS exchange, not a financial metric clock or execution causality.
"""
from dataclasses import dataclass,field
from decimal import Decimal
from email.utils import parsedate_to_datetime
import hashlib
import json
import urllib.parse
import urllib.request

from .plan_detail import _NoRedirect
from .plan_readback import _clock,_pairs,_invalid_constant

CREATE_PATH='/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/create/'
LIST_PATH='/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/list/'
STATUS_PATH='/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/status/update/'
_SEAL=object()


def digest(value):return hashlib.sha256(value.encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class _NativeReply:
    raw:str
    method:str
    path:str
    request_digest:str
    sent_at:object
    received_at:object
    server_at:object
    request_id_digest:object
    clock_valid:bool
    _seal:object=field(repr=False,compare=False)

    def payload(self):
        return json.loads(self.raw,parse_float=Decimal,object_pairs_hook=_pairs,parse_constant=_invalid_constant)


def qualified(reply):
    return (isinstance(reply,_NativeReply) and reply._seal is _SEAL and reply.clock_valid is True
        and reply.server_at is not None and isinstance(reply.request_id_digest,str))


def task_url(params,page):
    allowed={'advertiser_id','marketing_goal','ad_id','start_time','end_time','scene','filtering','order_type','order_field','page_size'}
    if not isinstance(params,dict) or set(params)!=allowed or type(page) is not int or not 1<=page<=20:
        raise ValueError('原生任务分页字段或范围无效')
    return 'https://api.oceanengine.com'+LIST_PATH+'?'+urllib.parse.urlencode({**params,'page':page})


def _exchange(method,path,url,body,token,timeout,clock,*,before_send=None):
    sent=_clock(clock())
    if before_send is not None:before_send(sent)
    headers={'Access-Token':token,'Cache-Control':'no-cache'}
    if method=='POST':headers['Content-Type']='application/json'
    request=urllib.request.Request(url,data=body.encode('utf-8') if body is not None else None,headers=headers,method=method)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),_NoRedirect())
    with opener.open(request,timeout=timeout) as response:
        if response.geturl()!=url:raise ValueError('原生响应地址变化')
        raw=response.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('原生控制响应超过1MiB')
        date_header=response.headers.get('Date')
    text=raw.decode('utf-8');received=_clock(clock())
    payload=json.loads(text,parse_float=Decimal,object_pairs_hook=_pairs,parse_constant=_invalid_constant)
    if not isinstance(payload,dict):raise ValueError('原生业务响应不是对象')
    rid=payload.get('request_id');rid_hash=None
    if isinstance(rid,str) and 0<len(rid)<=200 and all(33<=ord(c)<=126 for c in rid):rid_hash=digest(rid)
    server=None
    try:
        server=_clock(parsedate_to_datetime(date_header))
    except (ValueError,TypeError,AttributeError,OverflowError):pass
    valid=received>=sent and server is not None and abs((server-received).total_seconds())<=120 and abs((server-sent).total_seconds())<=120
    return _NativeReply(text,method,path,digest(body if body is not None else url),sent,received,server,rid_hash,valid,_SEAL)


def create_task(body,token,timeout,clock):
    return _exchange('POST',CREATE_PATH,'https://api.oceanengine.com'+CREATE_PATH,body,token,timeout,clock)


def read_task_page(params,page,token,timeout,clock):
    return _exchange('GET',LIST_PATH,task_url(params,page),None,token,timeout,clock)


def end_task(body,token,timeout,clock):
    """The caller must have claimed one concrete DISABLE intent before IO."""
    from .plan_readback import _business_data,_id
    parsed=_business_data('{"code":0,"data":'+body+'}')
    if (set(parsed)!={'advertiser_id','task_ids','opt_type'} or parsed['opt_type']!='DISABLE'
        or not isinstance(parsed['task_ids'],list) or len(parsed['task_ids'])!=1):
        raise ValueError('截止客户端只允许准确单任务的手动结束，不用于暂停、开启或删除')
    _id(parsed['advertiser_id']);_id(parsed['task_ids'][0])
    return _exchange('POST',STATUS_PATH,'https://api.oceanengine.com'+STATUS_PATH,body,token,timeout,clock)
