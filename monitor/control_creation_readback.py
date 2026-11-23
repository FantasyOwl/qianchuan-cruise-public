"""Bind creation receipts to exact task reads; supplied files never unlock IO."""
from dataclasses import dataclass
from datetime import datetime,timedelta
from decimal import Decimal
import hashlib
import json
import re

from .actuation import amount
from .control_creation import CONTRACT_VERSION,PURCHASE
from .control_readback import prepare_task_read_request,_page,STATUSES,SOURCE
from .creative_rotation import _exact
from .official_reports import _document,_guard
from .plan_readback import _clock,_id,_business_data
from .native_control_io import qualified,_NativeReply,read_task_page,task_url,digest,LIST_PATH
from .schema import now_cn,CN

FORMAT='qianchuan_control_creation_review_v1'


def _raw(value):
    if isinstance(value,str):return value
    if not isinstance(value,dict):raise ValueError('任务业务响应须为对象或JSON文本')
    try:return json.dumps(value,ensure_ascii=False,allow_nan=False)
    except (ValueError,TypeError):raise ValueError('任务业务响应数值无效') from None


def creation_intent(attempt):
    if not isinstance(attempt,dict) or not isinstance(attempt.get('context'),dict) or not isinstance(attempt.get('result'),dict):
        raise ValueError('缺少创建请求和接受回执记录')
    ctx,res=attempt['context'],attempt['result']
    if (attempt.get('state')!='unknown' or ctx.get('scope')!='control_create' or ctx.get('entity_id') is not None
        or ctx.get('control_type')!='one_click_boost' or ctx.get('action')!='one_click_boost'
        or ctx.get('contract')!='control_create' or ctx.get('contract_version')!=CONTRACT_VERSION
        or ctx.get('marketing_goal')!='LIVE_PROM_GOODS' or ctx.get('requested_external_action')!=PURCHASE):
        raise ValueError('创建记录的独立作用域、合同版本或目标不完整，不能猜测旧记录')
    aid,pid=_id(ctx.get('account_id')),_id(ctx.get('parent_plan_id'))
    if res.get('business_accepted') is not True or res.get('submitted') is not True or res.get('parent_plan_id')!=pid:
        raise ValueError('没有已接受且明确归属母计划的新任务ID，不能按名称寻找或重建')
    task=_id(res.get('created_task_id'));body=ctx.get('request_body_json')
    if not isinstance(body,str) or len(body.encode())>4096 or digest(body)!=ctx.get('request_digest'):
        raise ValueError('创建请求原文或摘要不一致')
    # Reuse the strict native parser with a local envelope, not a caller success flag.
    parsed=_business_data('{"code":0,"data":'+body+'}')
    if set(parsed)!={'advertiser_id','ad_id','scene','budget','duration','external_action'}:
        raise ValueError('创建请求字段与已核验的一键起量合同不符')
    if _id(parsed['advertiser_id'])!=aid or _id(parsed['ad_id'])!=pid or parsed['scene']!='SMART_BOOST' or parsed['external_action']!=PURCHASE:
        raise ValueError('创建请求与回执作用域不符')
    budget,duration=amount(parsed['budget']),amount(parsed['duration'])
    if not Decimal(100)<=budget<=Decimal(300) or not Decimal(1)<=duration<=Decimal(24) or duration%Decimal('.5'):
        raise ValueError('创建预算或时长不在当前用户策略和官方网格内')
    if amount(ctx.get('requested_budget'))!=budget or amount(ctx.get('requested_duration_hours'))!=duration:
        raise ValueError('创建记录的意图与原始请求不一致')
    claimed=_clock(attempt.get('created_at'));receipt=res.get('creation_receipt',{})
    if not isinstance(receipt,dict):raise ValueError('创建原生接收记录无效')
    received=_clock(receipt['received_at']) if receipt.get('received_at') is not None else None
    server=_clock(receipt['server_at']) if receipt.get('server_at') is not None else None
    sent=_clock(receipt['sent_at']) if receipt.get('sent_at') is not None else None
    if received is not None and received<claimed:raise ValueError('创建接收时间早于占用时间')
    trusted=(receipt.get('origin_verified') is True and receipt.get('request_digest')==ctx['request_digest']
        and received is not None and server is not None and sent is not None and claimed<=sent<=received
        and abs((received-server).total_seconds())<=120 and abs((sent-server).total_seconds())<=120
        and all(isinstance(receipt.get(k),str) and re.fullmatch(r'[0-9a-f]{64}',receipt[k]) for k in ('request_id_digest','response_digest')))
    cooldown=ctx.get('cooldown_seconds')
    if type(cooldown) not in {int,float} or not 3600<=cooldown<=86400:raise ValueError('创建母计划冷却时间无效')
    return {'account_id':aid,'parent_plan_id':pid,'task_id':task,'budget':format(budget,'.2f'),
        'duration_hours':format(duration,'.1f'),'claimed_at':claimed,
        'receipt_at':received,'trusted_creation_receipt':trusted,
        'cooldown_anchor':max(received,server) if trusted else None,
        'minimum_hold_seconds':max(cooldown,float(duration)*3600)+120,
        'intent_digest':digest(json.dumps(attempt,ensure_ascii=True,sort_keys=True,separators=(',',':')))}


def prepare_creation_read_request(attempt,settings,start,end,*,max_pages=5,page_size=100):
    intent=creation_intent(attempt)
    request=prepare_task_read_request(intent['account_id'],intent['parent_plan_id'],intent['task_id'],'one_click_boost',start,end,settings,max_pages=max_pages,page_size=page_size)
    if request['marketing_goal']!='LIVE_PROM_GOODS':raise ValueError('当前账户场景已改变，不能套用创建时的查询')
    filtering=json.loads(request['params']['filtering']);filtering['having_cost']='ALL'
    request['params']['filtering']=json.dumps(filtering,separators=(',',':'))
    request['digest']=digest(json.dumps({'params':request['params'],'max_pages':max_pages,'intent':intent['intent_digest']},sort_keys=True,separators=(',',':')))
    return request


def compare_creation_task_pages(raw_pages,query,attempt,now):
    intent=creation_intent(attempt)
    keys={'account_id','parent_plan_id','marketing_goal','scene','task_id','window_start','window_end','request_started_at','received_at','max_pages','page_size'}
    _exact(query,keys);now,started,received=map(_clock,(now,query['request_started_at'],query['received_at']))
    start,end=map(_clock,(query['window_start'],query['window_end']))
    if any(_id(query[k])!=intent[k] for k in ('account_id','parent_plan_id','task_id')) or query['marketing_goal']!='LIVE_PROM_GOODS' or query['scene']!='SMART_BOOST':
        raise ValueError('新任务读取与原创建身份、场景不一致')
    reference=intent['receipt_at'] or intent['claimed_at']
    if (not intent['claimed_at']<=reference<=started<=received<=now or not start<=reference<=end<=started
        or not timedelta(0)<end-start<=timedelta(days=7) or (now-received).total_seconds()>600 or (received-started).total_seconds()>600):
        raise ValueError('新任务读取的时序、窗口或时效不符')
    if type(query['max_pages']) is not int or not 1<=query['max_pages']<=20 or type(query['page_size']) is not int or query['page_size'] not in {10,20,50,100}:
        raise ValueError('新任务读取分页范围无效')
    if not isinstance(raw_pages,list) or not 1<=len(raw_pages)<=query['max_pages']:raise ValueError('须提供从第一页开始的连续任务响应')
    rows={};total=pages=None;raws=[]
    for page,value in enumerate(raw_pages,1):
        raw=_raw(value);raws.append(raw);batch,n,p=_page(raw,page,query['page_size'])
        if total is not None and (n,p)!=(total,pages):raise ValueError('新任务读取期间分页数量变化')
        total,pages=n,p
        for row in batch:
            if not isinstance(row,dict):raise ValueError('新任务列表行结构无效')
            tid=_id(row.get('id'))
            if tid in rows:raise ValueError('新任务ID跨页重复，不能选择有利状态')
            rows[tid]=row
    exhausted=len(raws)==max(1,pages);block=[];row=rows.get(intent['task_id'])
    result={'account_id':intent['account_id'],'parent_plan_id':intent['parent_plan_id'],'task_id':intent['task_id'],
        'status':'inconclusive','expected_budget':intent['budget'],'expected_duration_hours':intent['duration_hours'],
        'observed_budget':None,'observed_duration_hours':None,'reported_task_status':None,
        'reported_create_time':None,'platform_created_at':None,'goal_label_matches':False,
        'blockers':block,'query_context':query,'checked_at':now.isoformat(),
        'pages_received':len(raws),'reported_total_pages':pages,'reported_total_number':total,'pagination_exhausted':exhausted,
        'inventory_complete':False,'provenance_verified':False,'platform_state_observed':False,
        'execution_confirmed':False,'can_release_hold':False,'automatic_retry':False,
        'response_digest':digest(json.dumps([digest(r) for r in raws],separators=(',',':'))),
        'contract_source':SOURCE,'source':'supplied_creation_task_pages'}
    if not exhausted:block.append('尚未读完本次查询的全部声明页')
    if row is None:block.append('没有准确新任务ID，不能按名称或列表缺失推断失败/结束');return result
    state=row.get('task_status')
    if not isinstance(state,str) or state not in STATUSES:block.append('任务状态不在当前已核验枚举中')
    else:result['reported_task_status']=state
    try:
        observed_budget,observed_duration=amount(row.get('budget')),amount(row.get('duration'))
        if not Decimal('.5')<=observed_duration<=Decimal(24) or observed_duration%Decimal('.5'):raise ValueError
        result.update(observed_budget=format(observed_budget,'.2f'),observed_duration_hours=format(observed_duration,'.1f'))
    except (ValueError,TypeError):block.append('返回预算或时长不是明确的原生数值')
    result['reported_create_time']=row.get('create_time') if isinstance(row.get('create_time'),str) else None
    result['goal_label_matches']=row.get('external_action_name')=='直播间支付' and 'deep_external_action_name' in row and row['deep_external_action_name'] is None
    if not result['goal_label_matches']:block.append('购买目标及明确的空深度目标名称尚未匹配')
    if not block:
        match=result['observed_budget']==intent['budget'] and result['observed_duration_hours']==intent['duration_hours']
        result['status']='matches_requested_configuration' if match else 'differs_requested_configuration'
    return result


def review_supplied_creation(payload,settings,now=None):
    payload=_document(payload);_guard(payload);_exact(payload,{'format','attempt','query_context','raw_pages'})
    if payload['format']!=FORMAT:raise ValueError('创建后任务核对格式不符')
    query=payload['query_context']
    if not isinstance(query,dict):raise ValueError('创建后核对上下文无效')
    prepare_creation_read_request(payload['attempt'],settings,query.get('window_start'),query.get('window_end'),max_pages=query.get('max_pages'),page_size=query.get('page_size'))
    result=compare_creation_task_pages(payload['raw_pages'],query,payload['attempt'],now or now_cn())
    return {'format':FORMAT,'comparison':result,'persisted':False,'executed':False,'ai_sent':False,
        'attempt_state_changed':False,'can_release_hold':False,
        'limits':['文件只比较明确创建意图与准确新任务ID，不认证原生来源或执行因果。',
            '任务创建时间字符串不代替带时区的平台状态时钟；查询末页不代表全量任务清单。',
            '文件中的来源/回执声明不能解除母计划占用；没有ID的未知请求不按相似名称自动恢复。']}


def load_creation_attempt(ledger,attempt_id):
    if not isinstance(attempt_id,str) or not 1<=len(attempt_id)<=150:raise ValueError('创建尝试ID无效')
    with ledger.connect() as db:
        row=db.execute('SELECT created_at,state,context_json,result_json FROM submission_attempts WHERE id=?',(attempt_id,)).fetchone()
    if row is None:raise ValueError('找不到创建尝试')
    try:return {'created_at':row[0],'state':row[1],'context':json.loads(row[2] or '{}'),'result':json.loads(row[3] or '{}')}
    except (TypeError,ValueError):raise ValueError('创建尝试记录损坏') from None


@dataclass(frozen=True)
class CreationTaskReadPermit:
    account_id:str
    request_digest:str
    issued_at:datetime
    expires_at:datetime
    authorization_reference:str
    allow_reconcile:bool=False


def _record_native_review(ledger,attempt_id,expected_attempt,request,replies,query,now,*,allow_reconcile,authorization_digest):
    """Recompute from raw receipts inside the transaction, never accept a verdict."""
    raw_pages=[r.raw if isinstance(r,_NativeReply) else _raw(r) for r in replies]
    intent=creation_intent(expected_attempt)
    comparison=compare_creation_task_pages(raw_pages,query,expected_attempt,now)
    native=all(qualified(r) and r.method=='GET' and r.path==LIST_PATH
        and r.request_digest==digest(task_url(request['params'],page))
        and _clock(query['request_started_at'])<=r.sent_at<=r.received_at<=now
        for page,r in enumerate(replies,1))
    if native:
        native=all(a.received_at<=b.sent_at for a,b in zip(replies,replies[1:]))
        request_ids=[r.request_id_digest for r in replies]
        native=native and len(set(request_ids))==len(request_ids)
    else:request_ids=[]
    received=_clock(query['received_at'])
    identity={'intent':intent['intent_digest'],'response':comparison['response_digest'],'query':query,'native_request_ids':request_ids}
    observation_id=digest(json.dumps(identity,ensure_ascii=True,sort_keys=True,separators=(',',':')))
    observed={'comparison':comparison,'received_at':received.isoformat(),'request_started_at':query['request_started_at'],
        'trusted_origin':native,'native_request_id_digests':request_ids,'intent_digest':intent['intent_digest'],
        'authorization_reference_digest':authorization_digest,'creator_receipt_verified':intent['trusted_creation_receipt']}
    changed=False;reason='independent_terminal_evidence_not_complete';support=[]
    with ledger.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT created_at,state,context_json,result_json FROM submission_attempts WHERE id=?',(attempt_id,)).fetchone()
        if current is None:raise ValueError('创建尝试消失')
        current_attempt={'created_at':current[0],'state':current[1],'context':json.loads(current[2] or '{}'),'result':json.loads(current[3] or '{}')}
        if current_attempt!=expected_attempt:raise ValueError('读取期间创建记录已改变，请检查当前账本')
        prior=db.execute('SELECT observation_json FROM control_creation_observations WHERE attempt_id=? AND observation_id=?',(attempt_id,observation_id)).fetchone()
        if prior is None:
            db.execute('INSERT INTO control_creation_observations VALUES(?,?,?,?,?)',
                (attempt_id,observation_id,received.isoformat(),1 if native else 0,json.dumps(observed,ensure_ascii=False,allow_nan=False)))
        if allow_reconcile is not True:reason='read_authorized_without_state_transition'
        elif not intent['trusted_creation_receipt']:reason='creation_origin_not_verified'
        elif not native:reason='read_origin_not_verified'
        elif comparison['status']!='matches_requested_configuration' or comparison['reported_task_status']!='FROZEN':reason='matching_explicit_frozen_state_required'
        elif (received-intent['cooldown_anchor']).total_seconds()<intent['minimum_hold_seconds']:reason='creation_cooldown_not_elapsed'
        else:
            old_rows=db.execute('SELECT observation_id,observation_json FROM control_creation_observations WHERE attempt_id=? AND trusted_origin=1',(attempt_id,)).fetchall()
            previous=[(old_id,json.loads(value)) for old_id,value in old_rows if old_id!=observation_id]
            previous=[(old_id,old) for old_id,old in previous if old.get('intent_digest')==intent['intent_digest']]
            if previous:
                latest=max(_clock(old['received_at']) for _,old in previous)
                peers=[(old_id,old) for old_id,old in previous if _clock(old['received_at'])==latest]
                # Never skip a newer running/conflicting/incomplete native read.
                compatible=all(old.get('creator_receipt_verified') is True
                    and old.get('comparison',{}).get('status')=='matches_requested_configuration'
                    and old.get('comparison',{}).get('reported_task_status')=='FROZEN'
                    and not set(old.get('native_request_id_digests',[]))&set(request_ids) for _,old in peers)
                if compatible and (_clock(query['request_started_at'])-latest).total_seconds()>=120 and (received-latest).total_seconds()<=600:
                    support=[peers[0][0],observation_id]
            if support:
                updated={**expected_attempt['result'],'state':'reconciled_terminal','ledger_state':'reconciled_terminal',
                    'parent_hold_active':False,'platform_state_observed':True,
                    'reconciliation':{'kind':'two_direct_reads_report_frozen','at':received.isoformat(),
                        'observation_ids':support,'platform_reported_state':'FROZEN','execution_causality_verified':False,
                        'platform_metric_clock_verified':False,'automatic_retry':False}}
                changed=db.execute("UPDATE submission_attempts SET state='reconciled_terminal',result_json=? WHERE id=? AND state='unknown'",
                    (json.dumps(updated,ensure_ascii=False,allow_nan=False),attempt_id)).rowcount==1
                reason='explicit_frozen_state_reconciled' if changed else 'attempt_changed'
    return {'comparison':comparison,'observation_id':observation_id,'native_read_origin_verified':native,
        'creator_receipt_verified':intent['trusted_creation_receipt'],'attempt_state_changed':changed,
        'parent_hold_active':not changed,'state_transition_reason':reason,'supporting_observation_ids':support,
        'automatic_retry':False,'account_mutations':0}


class CreationTaskReadClient:
    """Default-off GET and local reconciliation, no task mutation method."""
    def __init__(self,*,enable_network=False,transport=None,clock=None):
        if transport is not None and not callable(transport):raise ValueError('创建后读取传输器无效')
        if clock is not None and not callable(clock):raise ValueError('创建后读取时钟无效')
        self.enabled=enable_network is True;self.transport=transport;self.clock=now_cn if clock is None else clock

    def review_attempt(self,ledger,attempt_id,settings,permit,token_provider,start,end,*,max_pages=5,page_size=100):
        if not self.enabled:raise PermissionError('创建后读取默认关闭，观察服务没有原生读取或解锁入口')
        began=_clock(self.clock())
        if (not isinstance(permit,CreationTaskReadPermit) or not isinstance(permit.issued_at,datetime)
            or not isinstance(permit.expires_at,datetime) or permit.issued_at.tzinfo is None or permit.expires_at.tzinfo is None
            or not permit.issued_at<=began<permit.expires_at or not timedelta(0)<permit.expires_at-permit.issued_at<=timedelta(minutes=5)
            or not isinstance(permit.authorization_reference,str) or not permit.authorization_reference.strip()):
            raise PermissionError('需要具体创建任务及查询的有效读取授权记录')
        attempt=load_creation_attempt(ledger,attempt_id)
        request=prepare_creation_read_request(attempt,settings,start,end,max_pages=max_pages,page_size=page_size)
        if permit.account_id!=request['account_id'] or permit.request_digest!=request['digest'] or _clock(request['window_end'])>began:
            raise PermissionError('创建后读取范围与授权不符')
        try:token=token_provider()
        except Exception:raise ValueError('显式凭据提供器未返回可用凭据') from None
        if not isinstance(token,str) or not 1<=len(token)<=4096 or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError('开放平台凭据格式无效')
        replies=[];first_io=None;last=began;total=None;size=0
        try:
            for page in range(1,max_pages+1):
                at=_clock(self.clock())
                if not last<=at<permit.expires_at:raise ValueError('读取时钟或授权无效')
                current=prepare_creation_read_request(attempt,settings,start,end,max_pages=max_pages,page_size=page_size)
                if current['digest']!=request['digest']:raise ValueError('读取期间账户配置改变')
                first_io=at if first_io is None else first_io
                reply=(read_task_page(dict(request['params']),page,token,10,self.clock) if self.transport is None
                    else self.transport(dict(request['params']),page,token,10))
                raw=reply.raw if isinstance(reply,_NativeReply) else _raw(reply);size+=len(raw.encode())
                if size>2*1024*1024:raise ValueError('创建后读取累计响应超过2MiB')
                _,_,pages=_page(raw,page,page_size)
                if total is not None and pages!=total:raise ValueError('分页总数变化')
                total=pages;replies.append(reply if isinstance(reply,_NativeReply) else raw);last=at
                if page>=max(1,total):break
            received=_clock(self.clock())
            if not last<=received<permit.expires_at:raise ValueError('读取结束时钟或授权无效')
            current=prepare_creation_read_request(attempt,settings,start,end,max_pages=max_pages,page_size=page_size)
            if current['digest']!=request['digest']:raise ValueError('读取完成时账户或查询范围已改变')
            query={k:request[k] for k in ('account_id','parent_plan_id','marketing_goal','scene','task_id','window_start','window_end','max_pages','page_size')}
            query.update(request_started_at=first_io.isoformat(),received_at=received.isoformat())
            result=_record_native_review(ledger,attempt_id,attempt,request,replies,query,received,
                allow_reconcile=permit.allow_reconcile,authorization_digest=digest(permit.authorization_reference))
        except Exception:
            return {'ok':False,'read_attempted':True,'read_only':True,'automatic_retry':False,'attempt_state_changed':False,
                'note':'创建后读取或核对未完成；不保留原始异常，不自动重试，请检查当前本地账本状态'}
        return {'ok':True,'read_attempted':True,'read_only':True,**result}
