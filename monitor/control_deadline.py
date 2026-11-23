"""Durable end deadlines, disconnected from serving and default-off native IO.

Reservation is not a creation authorization. In particular, this module does
not remove the block on cost-controlled material-chase creation. A future
creator must reserve before IO and bind the actual sealed creation reply.
"""
import copy
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime,timedelta
from pathlib import Path

from .control_contracts import interpret_control_status_response
from .control_creation import interpret_creation_response
from .control_readback import prepare_task_read_request,_page,STATUSES,SCENES
from .native_control_io import (qualified,_NativeReply,CREATE_PATH,LIST_PATH,STATUS_PATH,
    digest,task_url,read_task_page,end_task)
from .plan_locks import check_reservations,check_attempts
from .plan_readback import _clock,_id,_business_data
from .schema import now_cn

TERMINAL={'closed_observed','cancelled_before_creation','cancelled_creation_rejected'}
TRANSITIONS={
    'reserved_before_creation':{'watching','creation_identity_unknown','cancelled_before_creation','cancelled_creation_rejected'},
    'creation_identity_unknown':{'creation_identity_unknown'},
    'watching':{'watching','end_claimed','closed_observed'},
    'end_claimed':{'end_claimed','end_unknown','end_rejected','end_accepted_pending_readback','closed_observed'},
    'end_unknown':{'end_unknown','closed_observed'},'end_rejected':{'end_rejected','closed_observed'},
    'end_accepted_pending_readback':{'end_accepted_pending_readback','closed_observed'},
}


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))


def _key(value):
    if not isinstance(value,str) or not 1<=len(value)<=100 or not value.isascii() or not all(c.isalnum() or c in '_-' for c in value):
        raise ValueError('截止记录编号无效')
    return value


class DeadlineLedger:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS control_deadlines(
                    id TEXT PRIMARY KEY,account_id TEXT NOT NULL,parent_plan_id TEXT NOT NULL,
                    material_id TEXT,version INTEGER NOT NULL,state TEXT NOT NULL,
                    released_at TEXT,data_json TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS active_control_deadline_parent
                    ON control_deadlines(account_id,parent_plan_id) WHERE released_at IS NULL;
                CREATE TABLE IF NOT EXISTS control_deadline_journal(
                    deadline_id TEXT NOT NULL,seq INTEGER NOT NULL,at TEXT NOT NULL,event TEXT NOT NULL,
                    details_json TEXT NOT NULL,PRIMARY KEY(deadline_id,seq));
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(str(self.path),timeout=5)
        try:
            with db:yield db
        finally:db.close()

    def get(self,job_id):
        with self.connect() as db:row=db.execute('SELECT data_json FROM control_deadlines WHERE id=?',(_key(job_id),)).fetchone()
        if row is None:raise ValueError('找不到截止记录')
        return json.loads(row[0])

    def ready_for_creation(self,job_id,body,now):
        old=self.get(job_id);at=_clock(now)
        if (old['state']!='reserved_before_creation' or digest(body)!=old['creation_request_digest']
            or not 0<=(at-_clock(old['reserved_at'])).total_seconds()<=60):
            raise ValueError('截止预留已变化或超过发送窗口，不能发起创建')
        return old

    def reserve(self,job_id,account_id,parent_plan_id,control_type,material_id,duration_hours,request_digest,now):
        job_id=_key(job_id);aid,pid=map(_id,(account_id,parent_plan_id));at=_clock(now)
        if not isinstance(control_type,str) or control_type not in SCENES:raise ValueError('截止只用于已核验的起量/素材追投任务')
        if type(duration_hours) not in {int,float} or not 1<=duration_hours<=24 or duration_hours*2%1:
            raise ValueError('截止时长须为明确的半小时网格')
        if control_type=='material_chase':
            material_id=_id(material_id)
            if not 2<=duration_hours<=4:raise ValueError('素材追投截止须保持用户2至4小时测试策略')
        elif material_id is not None:raise ValueError('一键起量不按素材伪造追投身份')
        if not isinstance(request_digest,str) or len(request_digest)!=64 or any(c not in '0123456789abcdef' for c in request_digest):
            raise ValueError('截止须绑定具体创建请求摘要')
        record={'id':job_id,'account_id':aid,'parent_plan_id':pid,'control_type':control_type,'material_id':material_id,
            'creation_request_digest':request_digest,'duration_hours':duration_hours,'reserved_at':at.isoformat(),
            'due_at':(at+timedelta(hours=duration_hours)).isoformat(),'deadline_anchor_at':None,'updated_at':at.isoformat(),
            'version':1,'state':'reserved_before_creation','task_id':None,'owner_attempt_id':None,
            'native_creation_verified':False,'creation_receipt':None,'end_attempted':False,'end_claimed_at':None,'latest_observation':None,
            'parent_hold_active':True,'automatic_retry':False,'platform_metric_clock_verified':False,
            'execution_causality_verified':False}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');check_reservations(db,(aid,pid));check_attempts(db,(aid,pid),at,3600)
            if material_id:
                for released,raw in db.execute('SELECT released_at,data_json FROM control_deadlines WHERE account_id=? AND material_id=?',(aid,material_id)):
                    if json.loads(raw)['state'] in {'cancelled_before_creation','cancelled_creation_rejected'}:continue
                    if released is None or (at-_clock(released)).total_seconds()<86400:
                        raise ValueError('该账户素材仍在截止占用或结束后24小时冷却中')
            try:db.execute('INSERT INTO control_deadlines VALUES(?,?,?,?,?,?,NULL,?)',(job_id,aid,pid,material_id,1,record['state'],_json(record)))
            except sqlite3.IntegrityError:raise ValueError('截止编号已用或母计划已有截止占用') from None
            self._journal(db,record,at,'reserved',{'creation_authorized':False})
        return record

    @staticmethod
    def _journal(db,record,at,event,details):
        db.execute('INSERT INTO control_deadline_journal VALUES(?,?,?,?,?)',(record['id'],record['version'],at.isoformat(),event,_json(details)))

    def change(self,old,state,now,updates=None,*,event,details=None,release=False):
        at=_clock(now)
        if at<_clock(old['updated_at']):raise ValueError('截止账本时间倒退')
        allowed={'task_id','owner_attempt_id','native_creation_verified','creation_receipt','deadline_anchor_at','due_at','end_attempted','end_claimed_at','latest_observation','end_receipt','closed_at'}
        if set(updates or {})-allowed:raise ValueError('不能修改截止原始身份、期限或请求摘要')
        if {'due_at','deadline_anchor_at'}&set(updates or {}):
            receipt=(updates or {}).get('creation_receipt',{})
            anchor=_clock(receipt.get('received_at'))
            if (old['state']!='reserved_before_creation' or state!='watching' or event!='native_creation_bound'
                or (updates or {}).get('deadline_anchor_at')!=anchor.isoformat()
                or (updates or {}).get('due_at')!=(anchor+timedelta(hours=old['duration_hours'])).isoformat()):
                raise ValueError('截止只能在首次原生接受绑定时按接收时点确定，之后不能延长')
        if old['state'] in TERMINAL:raise ValueError('截止记录已结束')
        if state not in TRANSITIONS.get(old['state'],set()):raise ValueError('截止阶段不允许该跳转')
        if release and state not in TERMINAL:raise ValueError('结果未知不能释放截止占用')
        value={**old,**copy.deepcopy(updates or {}),'state':state,'version':old['version']+1,'updated_at':at.isoformat(),
            'parent_hold_active':not release}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT data_json FROM control_deadlines WHERE id=?',(old['id'],)).fetchone()
            if row is None or json.loads(row[0])!=old:raise ValueError('截止记录已被其他工作进程修改')
            if state=='cancelled_before_creation' and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='submission_attempts'").fetchone():
                for attempt_state,raw_ctx,raw_result in db.execute('SELECT state,context_json,result_json FROM submission_attempts'):
                    ctx=json.loads(raw_ctx or '{}');res=json.loads(raw_result or '{}')
                    if ctx.get('deadline_id')==old['id'] and (attempt_state!='failed' or res.get('submitted') is not False):
                        raise ValueError('截止已交给可能发送的创建尝试，不能当作未创建取消')
            db.execute('UPDATE control_deadlines SET version=?,state=?,released_at=?,data_json=? WHERE id=?',
                (value['version'],state,at.isoformat() if release else None,_json(value),old['id']))
            self._journal(db,value,at,event,details or {})
            if state=='closed_observed' and old['owner_attempt_id'] is not None:
                attempt=db.execute('SELECT state,context_json,result_json FROM submission_attempts WHERE id=?',(old['owner_attempt_id'],)).fetchone()
                if attempt is not None:
                    ctx=json.loads(attempt[1] or '{}');res=json.loads(attempt[2] or '{}')
                    if (attempt[0] in {'claimed','unknown'} and ctx.get('deadline_id')==old['id']
                        and ctx.get('request_digest')==old['creation_request_digest']
                        and ctx.get('account_id')==old['account_id'] and ctx.get('parent_plan_id')==old['parent_plan_id']
                        and old['native_creation_verified'] is True and old['creation_receipt'] is not None
                        and res.get('created_task_id') in {None,old['task_id']}):
                        res.update(state='reconciled_terminal',ledger_state='reconciled_terminal',parent_hold_active=False,
                            deadline_reconciliation={'at':at.isoformat(),'deadline_id':old['id'],'execution_causality_verified':False,
                                'platform_metric_clock_verified':False,'automatic_retry':False})
                        db.execute("UPDATE submission_attempts SET state='reconciled_terminal',result_json=? WHERE id=?",(_json(res),old['owner_attempt_id']))
        return value

    def bind_creation(self,job_id,body,reply,now,*,owner_attempt_id=None):
        old=self.get(job_id);at=_clock(now)
        if old['state']!='reserved_before_creation':raise ValueError('截止不能重复绑定任务')
        if digest(body)!=old['creation_request_digest']:raise ValueError('创建请求与截止预留不一致')
        parsed=_business_data('{"code":0,"data":'+body+'}')
        if (_id(parsed.get('advertiser_id'))!=old['account_id'] or _id(parsed.get('ad_id'))!=old['parent_plan_id']
            or parsed.get('scene')!=SCENES[old['control_type']]):raise ValueError('创建身份或工具与截止不符')
        if old['control_type']=='material_chase' and parsed.get('material_ids')!=[int(old['material_id'])]:
            raise ValueError('当前截止只绑定明确的单素材追投，不拆分或猜测素材用途')
        if old['control_type']=='one_click_boost' and parsed.get('duration')!=old['duration_hours']:
            raise ValueError('起量原生时长与截止预留不一致')
        native=(qualified(reply) and reply.method=='POST' and reply.path==CREATE_PATH and reply.request_digest==digest(body)
            and _clock(old['reserved_at'])<=reply.sent_at<=reply.received_at<=at
            and (reply.sent_at-_clock(old['reserved_at'])).total_seconds()<=60
            and (at-_clock(old['reserved_at'])).total_seconds()<=120)
        decoded=interpret_creation_response(reply.payload() if isinstance(reply,_NativeReply) else reply)
        if native and decoded['known_rejected']:
            return self.change(old,'cancelled_creation_rejected',at,event='native_creation_rejected',release=True)
        if not native or not decoded['business_accepted']:
            return self.change(old,'creation_identity_unknown',at,event='creation_not_bound',details={'native_origin_verified':native})
        if owner_attempt_id is not None:
            _key(owner_attempt_id)
            with self.connect() as db:
                attempt=db.execute('SELECT state,context_json FROM submission_attempts WHERE id=?',(owner_attempt_id,)).fetchone()
            if attempt is None:raise ValueError('截止缺少明确创建尝试')
            ctx=json.loads(attempt[1] or '{}')
            if (attempt[0]!='claimed' or ctx.get('deadline_id')!=job_id or ctx.get('request_digest')!=digest(body)
                or ctx.get('account_id')!=old['account_id'] or ctx.get('parent_plan_id')!=old['parent_plan_id']):
                raise ValueError('截止与当前创建尝试不匹配')
        return self.change(old,'watching',at,{'task_id':decoded['created_task_id'],'native_creation_verified':True,
            'deadline_anchor_at':reply.received_at.isoformat(),
            'due_at':(reply.received_at+timedelta(hours=old['duration_hours'])).isoformat(),
            'owner_attempt_id':owner_attempt_id,'creation_receipt':{'request_digest':digest(body),'response_digest':digest(reply.raw),
                'request_id_digest':reply.request_id_digest,'sent_at':reply.sent_at.isoformat(),'received_at':reply.received_at.isoformat()}},
            event='native_creation_bound',details={'request_digest':digest(body)})

    def cancel_before_creation(self,job_id,now):
        old=self.get(job_id)
        if old['state']!='reserved_before_creation':raise ValueError('创建可能发生后不能取消截止或释放占用')
        return self.change(old,'cancelled_before_creation',now,event='cancelled_before_creation',release=True)


def deadline_preview(record,settings,now):
    at=_clock(now);aid,pid,tid=map(_id,(record['account_id'],record['parent_plan_id'],record.get('task_id')))
    start=_clock(record['reserved_at'])-timedelta(minutes=1)
    request=prepare_task_read_request(aid,pid,tid,record['control_type'],start,at,settings,max_pages=5,page_size=10)
    if request['marketing_goal']!='LIVE_PROM_GOODS':raise ValueError('当前截止绑定直播全域，不随账户场景变更查询')
    # Prevent zero-spend tasks from disappearing from the exact-ID query.
    filtering=json.loads(request['params']['filtering']);filtering['having_cost']='ALL'
    request['params']['filtering']=json.dumps(filtering,separators=(',',':'))
    body='{"advertiser_id":'+aid+',"task_ids":['+tid+'],"opt_type":"DISABLE"}'
    identity={k:record[k] for k in ('id','account_id','parent_plan_id','task_id','creation_request_digest','due_at','control_type','material_id')}
    return {'body_json':body,'digest':digest(_json({'identity':identity,'body':body})),
        'read_request':request,'due':at>=_clock(record['due_at']),'live_execution_allowed':False,
        'action':'end_control_at_deadline','opt_type':'DISABLE','automatic_retry':False}


@dataclass(frozen=True)
class DeadlinePermit:
    account_id:str
    request_digest:str
    issued_at:datetime
    expires_at:datetime
    authorization_reference:str
    allow_end:bool=False


class DeadlineClient:
    def __init__(self,ledger,*,enable_network=False,clock=None):
        if clock is not None and not callable(clock):raise ValueError('截止时钟必须可调用')
        self.ledger=ledger;self.enabled=enable_network is True;self.clock=now_cn if clock is None else clock

    def step(self,job_id,settings,permit,token_provider):
        if not self.enabled:raise PermissionError('截止客户端默认关闭，观察服务不调度真实关停')
        at=_clock(self.clock());old=self.ledger.get(job_id)
        if old['state'] in TERMINAL:return {'state':old['state'],'read_attempted':False,'end_attempted':False,'automatic_retry':False}
        if at<_clock(old['due_at']):return {'state':'not_due','read_attempted':False,'end_attempted':False,'automatic_retry':False}
        if old['native_creation_verified'] is not True or old['task_id'] is None:
            return {'state':'creation_identity_unknown','read_attempted':False,'end_attempted':False,'parent_hold_active':True,'automatic_retry':False}
        preview=deadline_preview(old,settings,at)
        if (not isinstance(permit,DeadlinePermit) or not isinstance(permit.issued_at,datetime)
            or not isinstance(permit.expires_at,datetime) or permit.issued_at.tzinfo is None or permit.expires_at.tzinfo is None
            or not permit.issued_at<=at<permit.expires_at or not timedelta(0)<permit.expires_at-permit.issued_at<=timedelta(minutes=5)
            or permit.account_id!=old['account_id'] or permit.request_digest!=preview['digest']
            or not isinstance(permit.authorization_reference,str) or not permit.authorization_reference.strip()):
            raise PermissionError('需要具体截止任务的有效读取/结束许可')
        try:token=token_provider()
        except Exception:raise ValueError('显式凭据提供器未返回可用凭据') from None
        if not isinstance(token,str) or not 1<=len(token)<=4096 or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError('凭据格式无效')
        began=at;claimed=False
        try:
            rows={};replies=[];pages=None
            for page in range(1,6):
                io=_clock(self.clock())
                if not began<=io<permit.expires_at:raise ValueError('截止读取时钟或许可失效')
                current=deadline_preview(old,settings,io)
                if current['digest']!=preview['digest']:raise ValueError('截止身份改变')
                params=current['read_request']['params']
                reply=read_task_page(params,page,token,10,self.clock)
                if not (qualified(reply) and reply.method=='GET' and reply.path==LIST_PATH and reply.request_digest==digest(task_url(params,page))
                    and io<=reply.sent_at<=reply.received_at<permit.expires_at):raise ValueError('截止读取没有合格原生交换来源')
                batch,total,count=_page(reply.raw,page,10)
                if pages is not None and pages!=(total,count):raise ValueError('截止查询分页改变')
                pages=(total,count);replies.append(reply)
                if sum(len(r.raw.encode()) for r in replies)>2*1024*1024:raise ValueError('截止读取响应超限')
                for row in batch:
                    tid=_id(row.get('id'))
                    if tid in rows:raise ValueError('截止查询含重复ID')
                    rows[tid]=row
                if page>=max(1,count):break
            now=_clock(self.clock())
            if (len(replies)!=max(1,pages[1]) or not began<=replies[-1].received_at<=now<permit.expires_at
                or any(a.received_at>b.sent_at for a,b in zip(replies,replies[1:]))):raise ValueError('截止读取时序或分页不完整')
            ids=[r.request_id_digest for r in replies]
            if len(ids)!=len(set(ids)):raise ValueError('截止分页回执重复')
            state=rows.get(old['task_id'],{}).get('task_status')
            if state not in STATUSES:raise ValueError('未读到准确任务及已核验状态')
            if deadline_preview(old,settings,now)['digest']!=preview['digest']:raise ValueError('截止读取期间范围改变')
            observation={'at':now.isoformat(),'started_at':replies[0].sent_at.isoformat(),'status':state,
                'request_id_digests':ids,'response_digest':digest(_json([digest(r.raw) for r in replies]))}
            prior=old['latest_observation'];closed=False
            if state in {'FROZEN','DISABLE'} and prior and prior['status'] in {'FROZEN','DISABLE'}:
                age=(now-_clock(prior['at'])).total_seconds();gap=(replies[0].sent_at-_clock(prior['at'])).total_seconds()
                closed=120<=gap and age<=600 and not set(ids)&set(prior['request_id_digests'])
            if closed:
                updated=self.ledger.change(old,'closed_observed',now,{'latest_observation':observation,'closed_at':now.isoformat()},
                    event='two_direct_terminal_reads',details={'platform_reported_states':[prior['status'],state],
                        'execution_causality_verified':False,'platform_metric_clock_verified':False},release=True)
                return {'state':updated['state'],'read_attempted':True,'end_attempted':False,'parent_hold_active':False,
                    'platform_reported_status':state,'execution_causality_verified':False,'automatic_retry':False}
            updated=self.ledger.change(old,old['state'],now,{'latest_observation':observation},event='direct_state_read',details={'status':state})
            if state!='PROCESSING' or old['state']!='watching' or permit.allow_end is not True:
                return {'state':updated['state'],'read_attempted':True,'end_attempted':False,'parent_hold_active':True,
                    'platform_reported_status':state,'automatic_retry':False}
            # Claim before the single POST. A crash leaves end_claimed and is never resent.
            io=_clock(self.clock())
            if not now<=io<permit.expires_at or deadline_preview(updated,settings,io)['digest']!=preview['digest']:
                raise ValueError('截止结束前许可或账户范围改变')
            updated=self.ledger.change(updated,'end_claimed',io,{'end_attempted':True,'end_claimed_at':io.isoformat()},event='end_claimed',
                details={'body_digest':digest(preview['body_json']),'authorization_digest':digest(permit.authorization_reference)})
            claimed=True
            # Recheck after SQLite, before network.
            io=_clock(self.clock())
            if not _clock(updated['updated_at'])<=io<permit.expires_at:raise ValueError('截止结束前时钟失效')
            if deadline_preview(updated,settings,io)['digest']!=preview['digest']:raise ValueError('截止结束前范围改变')
            if self.ledger.get(job_id)!=updated:raise ValueError('截止结束前账本已经改变')
            reply=end_task(preview['body_json'],token,10,self.clock)
            decoded=interpret_control_status_response(reply.payload(),[old['task_id']])
            finished=_clock(self.clock())
            native=(qualified(reply) and reply.method=='POST' and reply.path==STATUS_PATH and reply.request_digest==digest(preview['body_json'])
                and io<=reply.sent_at<permit.expires_at and reply.received_at<=finished)
            accepted=native and decoded['success'];rejected=native and not decoded['unknown'] and bool(decoded['failed'])
            receipt={'native_origin_verified':native,'business_accepted':accepted,'response_digest':digest(reply.raw),
                'request_id_digest':reply.request_id_digest if native else None,
                'sent_at':reply.sent_at.isoformat() if native else None,'received_at':reply.received_at.isoformat() if native else None}
            final='end_accepted_pending_readback' if accepted else 'end_rejected' if rejected else 'end_unknown'
            result=self.ledger.change(updated,final,finished,{'end_receipt':receipt},event='end_receipt')
            return {'state':result['state'],'read_attempted':True,'end_attempted':True,'business_accepted':accepted,
                'parent_hold_active':True,'platform_state_observed':False,'automatic_retry':False}
        except Exception:
            # Do not downgrade a concurrently changed record or retry a claimed POST.
            try:
                current=self.ledger.get(job_id);expected=updated if 'updated' in locals() else old
                if current==expected:
                    self.ledger.change(current,'end_unknown' if claimed else current['state'],self.clock(),
                        {'latest_observation':None},event='end_result_unknown' if claimed else 'read_not_complete')
            except Exception:pass
            return {'state':'needs_independent_review','read_attempted':True,'end_attempted':claimed,
                'parent_hold_active':True,'automatic_retry':False,'note':'截止读取或结束核对未完成；检查本机账本，不自动重发'}
