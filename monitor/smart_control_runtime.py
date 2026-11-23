"""Disconnected native flow control with a durable, non-extending stop deadline.

No task ID is invented. A business reply never proves actual platform state or
releases a parent hold. The observation server does not import this module.
"""
import copy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime,timedelta
import json
from pathlib import Path
import sqlite3
import threading
import time

from .accounts import registry_accounts
from .actuation import prepare_action,amount
from .evaluation import evaluate_all
from .native_control_io import _exchange,qualified,digest
from .plan_locks import check_reservations,check_attempts
from .plan_readback import _clock,_id,_business_data
from .schema import now_cn
from .smart_control import CREATE_PATH,STOP_PATH,interpret_smart_control_response

UNSENT={'reserved_before_creation'}
RELEASED={'cancelled_before_creation','creation_rejected'}
STOP_ATTEMPTED={'stop_claimed','stop_unknown','stop_rejected','stop_accepted_pending_readback'}


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))


def _key(value):
    if not isinstance(value,str) or not 1<=len(value)<=100 or not value.isascii() or not all(c.isalnum() or c in '_-' for c in value):
        raise ValueError('控量记录编号无效')
    return value


def _body(body,stop=False):
    value=_business_data('{"code":0,"data":'+body+'}')
    if set(value)!=({'advertiser_id','ad_id','opt_type'} if stop else {'advertiser_id','ad_id','budget'}):
        raise ValueError('控量请求只接受已核验的原生字段')
    aid,pid=_id(value['advertiser_id']),_id(value['ad_id'])
    if stop:
        if value['opt_type']!='DISABLE':raise ValueError('控量停止仅允许DISABLE')
    else:amount(value['budget'])
    return aid,pid


def _receipt(reply,path,body,anchor):
    valid=(qualified(reply) and reply.method=='POST' and reply.path==path
        and reply.request_digest==digest(body) and reply.sent_at==_clock(anchor)
        and reply.received_at>=reply.sent_at)
    decoded=interpret_smart_control_response(reply.payload()) if valid else {}
    return {'native_origin_verified':valid,'request_digest':digest(body),
        'response_digest':digest(reply.raw) if valid else None,
        'request_id_digest':reply.request_id_digest if valid else None,
        'sent_at':reply.sent_at.isoformat() if valid else None,
        'received_at':reply.received_at.isoformat() if valid else None,
        'business_accepted':decoded.get('business_accepted') is True,
        'known_rejected':decoded.get('known_rejected') is True}


class SmartControlLedger:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS smart_control_runs(
                    id TEXT PRIMARY KEY,account_id TEXT NOT NULL,parent_plan_id TEXT NOT NULL,
                    state TEXT NOT NULL,version INTEGER NOT NULL,released_at TEXT,data_json TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS smart_control_active_parent
                    ON smart_control_runs(account_id,parent_plan_id) WHERE released_at IS NULL;
                CREATE TABLE IF NOT EXISTS smart_control_journal(
                    run_id TEXT NOT NULL,seq INTEGER NOT NULL,at TEXT NOT NULL,event TEXT NOT NULL,
                    details_json TEXT NOT NULL,PRIMARY KEY(run_id,seq));
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(str(self.path),timeout=5)
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def _get(db,run_id):
        row=db.execute('SELECT data_json FROM smart_control_runs WHERE id=?',(_key(run_id),)).fetchone()
        if row is None:raise ValueError('找不到控量记录')
        return json.loads(row[0])

    def get(self,run_id):
        with self.connect() as db:return self._get(db,run_id)

    @staticmethod
    def _save(db,old,state,at,event,updates=None,*,release=False):
        if at<_clock(old['updated_at']):raise ValueError('控量账本时钟倒退')
        value={**old,**(updates or {}),'state':state,'version':old['version']+1,'updated_at':at.isoformat(),
            'parent_hold_active':not release}
        db.execute('UPDATE smart_control_runs SET state=?,version=?,released_at=?,data_json=? WHERE id=?',
            (state,value['version'],at.isoformat() if release else None,_json(value),old['id']))
        db.execute('INSERT INTO smart_control_journal VALUES(?,?,?,?,?)',
            (old['id'],value['version'],at.isoformat(),event,_json({'state':state})))
        return value

    def reserve(self,run_id,body,duration_minutes,now):
        run_id=_key(run_id);aid,pid=_body(body);at=_clock(now)
        if type(duration_minutes) is not int or not 30<=duration_minutes<=60:
            raise ValueError('控量本地时长须为30至60整分钟')
        stop='{"advertiser_id":'+aid+',"ad_id":'+pid+',"opt_type":"DISABLE"}'
        value={'id':run_id,'account_id':aid,'parent_plan_id':pid,'creation_body_json':body,
            'creation_request_digest':digest(body),'stop_body_json':stop,'duration_minutes':duration_minutes,
            'reserved_at':at.isoformat(),'updated_at':at.isoformat(),'version':1,'state':'reserved_before_creation',
            'first_create_request_sent_at':None,'due_at':None,'stop_sent_at':None,
            'creation_receipt':None,'stop_receipt':None,'parent_hold_active':True,
            'platform_state_observed':False,'automatic_retry':False}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');check_reservations(db,(aid,pid),now=at);check_attempts(db,(aid,pid),at,3600)
            try:db.execute('INSERT INTO smart_control_runs VALUES(?,?,?,?,1,NULL,?)',(run_id,aid,pid,value['state'],_json(value)))
            except sqlite3.IntegrityError:raise ValueError('控量编号已用或母计划已有占用') from None
            db.execute('INSERT INTO smart_control_journal VALUES(?,1,?,?,?)',(run_id,at.isoformat(),'reserved','{}'))
        return value

    def cancel_before_creation(self,run_id,now):
        at=_clock(now)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self._get(db,run_id)
            if old['state'] not in UNSENT:raise ValueError('控量可能发送后不能取消截止或释放占用')
            return self._save(db,old,'cancelled_before_creation',at,'cancelled',release=True)

    def claim_creation(self,run_id,body,now):
        """Called by the fixed HTTPS boundary before its first socket operation."""
        at=_clock(now)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self._get(db,run_id)
            if (old['state'] not in UNSENT or old['creation_body_json']!=body
                or not 0<=(at-_clock(old['reserved_at'])).total_seconds()<=60):
                raise ValueError('控量预留已变化或超过发送窗口，禁止重复创建')
            return self._save(db,old,'creation_claimed',at,'creation_claimed',{
                'first_create_request_sent_at':at.isoformat(),
                'due_at':(at+timedelta(minutes=old['duration_minutes'])).isoformat()})

    def finish_creation(self,run_id,reply,now):
        at=_clock(now)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self._get(db,run_id)
            if old['first_create_request_sent_at'] is None or old['state'] in RELEASED or old['creation_receipt'] is not None:
                raise ValueError('创建回执没有对应首次发送或已经记录')
            receipt=_receipt(reply,CREATE_PATH,old['creation_body_json'],old['first_create_request_sent_at'])
            state=('creation_rejected' if receipt['known_rejected'] else
                'creation_accepted_pending_readback' if receipt['business_accepted'] else 'creation_unknown')
            # A late create reply must not rewind a deadline stop or extend due_at.
            stopping=old['state'] in STOP_ATTEMPTED
            return self._save(db,old,old['state'] if stopping else state,at,'creation_reply',{'creation_receipt':receipt},
                release=not stopping and state=='creation_rejected')

    def claim_stop(self,run_id,now):
        at=_clock(now)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self._get(db,run_id)
            if (old['state'] not in {'creation_claimed','creation_unknown','creation_accepted_pending_readback'}
                or old['due_at'] is None or at<_clock(old['due_at'])):
                raise ValueError('控量尚未到期、未可能创建或已尝试停止')
            return self._save(db,old,'stop_claimed',at,'stop_claimed',{'stop_sent_at':at.isoformat()})

    def finish_stop(self,run_id,reply,now):
        at=_clock(now)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');old=self._get(db,run_id)
            if old['state']!='stop_claimed':raise ValueError('没有尚待记录的停止请求')
            receipt=_receipt(reply,STOP_PATH,old['stop_body_json'],old['stop_sent_at'])
            state=('stop_rejected' if receipt['known_rejected'] else
                'stop_accepted_pending_readback' if receipt['business_accepted'] else 'stop_unknown')
            return self._save(db,old,state,at,'stop_reply',{'stop_receipt':receipt})


@dataclass(frozen=True)
class SmartControlDelegation:
    run_id:str
    account_id:str
    parent_plan_id:str
    creation_request_digest:str
    duration_minutes:int
    issued_at:datetime
    expires_at:datetime
    authorization_reference:str
    allow_create:bool=False
    allow_parent_stop:bool=False


def _delegated(value,record,at,*,create=False):
    if (not isinstance(value,SmartControlDelegation)
        or (value.run_id,value.account_id,value.parent_plan_id,value.creation_request_digest)!=
            (record['id'],record['account_id'],record['parent_plan_id'],record['creation_request_digest'])
        or type(value.duration_minutes) is not int or value.duration_minutes!=record['duration_minutes']
        or not isinstance(value.issued_at,datetime) or not isinstance(value.expires_at,datetime)
        or value.issued_at.tzinfo is None or value.expires_at.tzinfo is None
        or not value.issued_at<=at<value.expires_at
        or not timedelta(0)<value.expires_at-value.issued_at<=timedelta(hours=3)
        or value.expires_at<(_clock(record['due_at']) if record['due_at'] else at+timedelta(minutes=record['duration_minutes']))+timedelta(minutes=5)
        or value.allow_parent_stop is not True or create and value.allow_create is not True
        or not isinstance(value.authorization_reference,str) or not value.authorization_reference.strip()):
        raise PermissionError('缺少该笔控量创建及按母计划停止的明确有效委托')
    return value


def _token(provider):
    try:token=provider()
    except Exception:raise ValueError('控量凭据提供器未返回可用凭据') from None
    if not isinstance(token,str) or not 1<=len(token)<=4096 or any(not 33<=ord(c)<=126 for c in token):
        raise ValueError('控量凭据格式无效')
    return token


def _scope(cfg,record):
    if len([a for a in registry_accounts(cfg) if a['id']==record['account_id'] and a['observe'] and a['scene']=='live'])!=1:
        raise PermissionError('控量当前账户范围已撤回或不是直播')


class SmartControlService:
    """Future explicit host must start this worker; import never starts a thread."""
    def __init__(self,ledger,settings_provider,delegation_provider,token_provider,*,enable_network=False,clock=None,interval=5):
        if type(interval) not in {int,float} or not 1<=interval<=30:raise ValueError('控量截止轮询间隔须为1至30秒')
        if clock is not None and not callable(clock):raise ValueError('控量时钟须可调用')
        self.ledger=ledger;self.settings_provider=settings_provider;self.delegation_provider=delegation_provider
        self.token_provider=token_provider;self.enabled=enable_network is True;self.clock=now_cn if clock is None else clock
        self.interval=interval;self._stop=threading.Event();self._started=threading.Event();self._thread=None
        self._tick_lock=threading.Lock();self._status_lock=threading.Lock();self._lifecycle_lock=threading.Lock()
        self._last_tick=None;self._last_monotonic=None;self._cycles=0;self._problems={}

    def _enabled(self):
        if not self.enabled:raise PermissionError('控量截止默认关闭，观察服务没有启动入口')
        if not all(callable(v) for v in (self.settings_provider,self.delegation_provider,self.token_provider)):
            raise ValueError('控量工作线程须显式提供配置、逐笔委托与凭据提供器')

    def _authorization(self,record,at,*,create=False):
        _scope(self.settings_provider(),record)
        return _delegated(self.delegation_provider(copy.deepcopy(record)),record,at,create=create)

    def require_ready(self,run_id,body,at):
        self._enabled()
        with self._status_lock:
            recent=(self._last_tick is not None and self._last_monotonic is not None
                and 0<=time.monotonic()-self._last_monotonic<=max(10,self.interval*2+2)
                and 0<=(at-self._last_tick).total_seconds()<=max(10,self.interval*2+2)
                and 'service' not in self._problems)
        if not self._thread or not self._thread.is_alive() or self._stop.is_set() or not recent:
            raise PermissionError('控量截止线程未运行或最近工作周期已过期')
        record=self.ledger.get(run_id)
        if (record['state'] not in UNSENT or record['creation_body_json']!=body
            or not 0<=(at-_clock(record['reserved_at'])).total_seconds()<=60):
            raise ValueError('控量截止预留已改变或超过发送窗口')
        self._authorization(record,at,create=True)
        return record

    def step(self,run_id):
        self._enabled();at=_clock(self.clock());record=self.ledger.get(run_id)
        if at<_clock(record['updated_at']):raise ValueError('控量截止时钟早于已持久记录，需要独立核对')
        if record['state'] in RELEASED:return {'state':record['state'],'stop_attempted':False}
        if record['state'] in STOP_ATTEMPTED:return {'state':'needs_independent_review','stop_attempted':False,'parent_hold_active':True}
        if record['due_at'] is None or at<_clock(record['due_at']):return {'state':'not_due','stop_attempted':False}
        self._authorization(record,at)
        token=_token(lambda:self.token_provider(copy.deepcopy(record)))
        began=at;claimed=False
        def before_send(sent):
            nonlocal claimed
            if self._stop.is_set() or sent<began:raise PermissionError('控量截止已停止或时钟倒退')
            self._authorization(self.ledger.get(run_id),sent)
            self.ledger.claim_stop(run_id,sent);claimed=True
        reply=None
        try:
            reply=_exchange('POST',STOP_PATH,'https://api.oceanengine.com'+STOP_PATH,
                record['stop_body_json'],token,10,self.clock,before_send=before_send)
        except Exception:pass
        current=self.ledger.get(run_id)
        if not claimed:
            if current['state'] in STOP_ATTEMPTED:
                return {'state':'needs_independent_review','stop_attempted':False,'parent_hold_active':True}
            raise ValueError('控量停止发送前复核未完成，没有发送停止请求')
        if claimed and current['state']=='stop_claimed':
            current=self.ledger.finish_stop(run_id,reply,_clock(self.clock()))
        return {'state':current['state'],'stop_attempted':current['stop_sent_at'] is not None,
            'parent_hold_active':current['parent_hold_active'],'platform_state_observed':False,'automatic_retry':False}

    def run_once(self):
        self._enabled()
        if self._stop.is_set():return {'stopped':True}
        if not self._tick_lock.acquire(blocking=False):return {'busy':True}
        problems={};results={}
        try:
            at=_clock(self.clock())
            with self._status_lock:
                if self._last_tick is not None and at<self._last_tick:raise ValueError('控量工作周期时钟倒退')
            with self.ledger.connect() as db:ids=[r[0] for r in db.execute('SELECT id FROM smart_control_runs WHERE released_at IS NULL ORDER BY id')]
            for run_id in ids:
                if self._stop.is_set():break
                try:
                    result=self.step(run_id);results[run_id]=result
                    if result.get('state') in STOP_ATTEMPTED|{'needs_independent_review'}:problems[run_id]='independent_review_needed'
                except Exception:problems[run_id]='authorization_scope_or_stop_not_ready'
            finished=_clock(self.clock())
            if finished<at:raise ValueError('控量工作时钟倒退')
            with self._status_lock:
                self._last_tick=finished;self._last_monotonic=time.monotonic();self._cycles+=1;self._problems=problems
            return {'results':results,'problems':problems,'jobs_checked':len(ids)}
        finally:self._tick_lock.release()

    def _loop(self):
        while not self._stop.is_set():
            try:self.run_once()
            except Exception:
                with self._status_lock:self._problems={'service':'cycle_not_complete'}
            self._started.set();self._stop.wait(self.interval)

    def start(self):
        self._enabled()
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():return self.status()
            self._stop.clear();self._started.clear()
            self._thread=threading.Thread(target=self._loop,name='qianchuan-smart-control-deadline',daemon=True);self._thread.start()
        self._started.wait(1)
        return self.status()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():self._thread.join(timeout=2)
        return self.status()

    def status(self):
        with self._status_lock:return {'enabled':self.enabled,'thread_alive':bool(self._thread and self._thread.is_alive()),
            'last_cycle_at':self._last_tick.isoformat() if self._last_tick else None,'cycles':self._cycles,
            'problems':dict(self._problems),'automatic_post_retry':False,'future_uptime_verified':False,
            'platform_state_readback_available':False}


class SmartControlClient:
    def __init__(self,ledger,service,*,enable_network=False,clock=None):
        if clock is not None and not callable(clock):raise ValueError('控量创建时钟须可调用')
        self.ledger=ledger;self.service=service;self.enabled=enable_network is True;self.clock=now_cn if clock is None else clock

    def dispatch(self,run_id,proposal_id,snapshots,settings,token_provider,*,events=(),migrations=(),recovery_snapshots=None,recovery_events=None):
        if not self.enabled:raise PermissionError('控量创建默认关闭，观察服务没有创建入口')
        if not isinstance(self.service,SmartControlService) or self.service.ledger.path.resolve()!=self.ledger.path.resolve():
            raise PermissionError('控量创建及截止必须使用同一账本和工作线程')
        def materialize(value):return value if isinstance(value,list) else list(value)
        snapshots,events,migrations=map(materialize,(snapshots,events,migrations))
        recovery_snapshots=None if recovery_snapshots is None else materialize(recovery_snapshots)
        recovery_events=None if recovery_events is None else materialize(recovery_events)
        began=_clock(self.clock())
        def reviewed(at):
            if at<began:raise ValueError('控量创建时钟倒退')
            rows,cfg,ev,mi,rr,re=copy.deepcopy((snapshots,settings,events,migrations,recovery_snapshots,recovery_events))
            evaluation=evaluate_all(rows,cfg,at,events=ev,migrations=mi,recovery_snapshots=rr,recovery_events=re)
            matches=[p for p in evaluation['recommendations'] if p['id']==proposal_id and p['action']=='one_click_control' and p['eligible'] is True]
            if len(matches)!=1:raise ValueError('当前完整评估没有唯一合格控量建议')
            proposal=matches[0]
            entities=[e for e in evaluation['entities'] if e['account_id']==proposal['account_id'] and e['scope']=='plan'
                and e['entity_id']==proposal['entity_id'] and e['source_key']==proposal['source_key']]
            current=[r for r in rows if len(entities)==1 and r['id']==entities[0]['latest_snapshot_id']]
            if len(current)!=1:raise ValueError('控量母计划最新来源不明确')
            preview=prepare_action(proposal,current[0],cfg,at,snapshots=rows,evaluation=evaluation)
            if not preview['supported_for_preview']:raise ValueError('控量完整资格与原生合同不足')
            record=self.service.require_ready(run_id,preview['request']['body_json'],at)
            if (record['account_id']!=proposal['account_id'] or record['parent_plan_id']!=proposal['entity_id']
                or record['duration_minutes']!=preview['local_duration_policy']['duration_minutes']):
                raise ValueError('控量当前请求及本地期限与预留不一致')
            return record
        record=reviewed(began);token=_token(token_provider)
        reviewed(_clock(self.clock()))
        claimed=False
        def before_send(sent):
            nonlocal claimed
            reviewed(sent);self.ledger.claim_creation(run_id,record['creation_body_json'],sent);claimed=True
        reply=None
        try:
            reply=_exchange('POST',CREATE_PATH,'https://api.oceanengine.com'+CREATE_PATH,
                record['creation_body_json'],token,10,self.clock,before_send=before_send)
        except Exception:
            if not claimed:raise ValueError('控量发送前复核未完成，没有发起请求') from None
        current=self.ledger.finish_creation(run_id,reply,_clock(self.clock()))
        return {'state':current['state'],'due_at':current['due_at'],'parent_hold_active':current['parent_hold_active'],
            'created_task_id':None,'platform_state_observed':False,'automatic_retry':False}
