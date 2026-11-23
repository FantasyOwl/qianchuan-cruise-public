"""Explicit future native deadline worker. No thread, IO or credential on import.

The observation app never starts this worker. A live private lease establishes
current worker readiness, not future uptime, platform permission or causality.
"""
import copy
from dataclasses import dataclass,field
from datetime import datetime,timedelta
from pathlib import Path
import threading
import time

from .accounts import registry_accounts
from .control_deadline import DeadlineClient,DeadlinePermit,deadline_preview,TERMINAL
from .plan_readback import _clock
from .schema import now_cn

_SEAL=object()


@dataclass(frozen=True)
class DeadlineDelegation:
    job_id:str
    account_id:str
    creation_request_digest:str
    issued_at:datetime
    expires_at:datetime
    authorization_reference:str
    allow_end:bool=False


@dataclass(frozen=True)
class DeadlineLease:
    job_id:str
    account_id:str
    request_digest:str
    issued_at:datetime
    expires_at:datetime
    _issued_monotonic:float=field(repr=False,compare=False)
    _owner:object=field(repr=False,compare=False)
    _seal:object=field(repr=False,compare=False)


def require_live_lease(lease,path,job_id,request_digest,now):
    at=_clock(now)
    if (not isinstance(lease,DeadlineLease) or lease._seal is not _SEAL
        or lease.job_id!=job_id or lease.request_digest!=request_digest
        or not lease.issued_at<=at<lease.expires_at
        or not 0<=time.monotonic()-lease._issued_monotonic<30
        or Path(path).resolve()!=lease._owner.ledger.path.resolve()):
        raise PermissionError('追投创建需要同库、具体请求和当前截止工作线程的私有短时租约')
    record,delegation=lease._owner._ready(job_id,request_digest,at)
    if record['account_id']!=lease.account_id or delegation.allow_end is not True:
        raise PermissionError('截止工作线程不再具备该具体任务的关停委托')
    return record


class DeadlineService:
    def __init__(self,ledger,settings_provider,delegation_provider,token_provider,*,enable_network=False,clock=None,interval=5):
        if type(interval) not in {int,float} or not 1<=interval<=30:raise ValueError('截止工作轮询间隔须为1至30秒')
        if clock is not None and not callable(clock):raise ValueError('截止工作时钟须可调用')
        self.ledger=ledger;self.settings_provider=settings_provider;self.delegation_provider=delegation_provider
        self.token_provider=token_provider;self.enabled=enable_network is True;self.clock=now_cn if clock is None else clock
        self.interval=interval;self._stop=threading.Event();self._started=threading.Event();self._thread=None
        self._tick_lock=threading.Lock();self._status_lock=threading.Lock();self._last_tick=None;self._last_monotonic=None
        self._lifecycle_lock=threading.Lock()
        self._cycles=0;self._problems={};self._last_results={}

    def _check_enabled(self):
        if not self.enabled:raise PermissionError('原生截止工作默认关闭，当前观察服务没有启动入口')
        if not all(callable(v) for v in (self.settings_provider,self.delegation_provider,self.token_provider)):
            raise ValueError('截止工作须显式提供配置、逐任务关停委托及凭据提供器')

    def _delegation(self,record,at,*,require_end=True):
        try:value=self.delegation_provider(copy.deepcopy(record))
        except Exception:raise PermissionError('明确关停委托提供器未返回可用记录') from None
        if (not isinstance(value,DeadlineDelegation) or value.job_id!=record['id'] or value.account_id!=record['account_id']
            or value.creation_request_digest!=record['creation_request_digest'] or require_end and value.allow_end is not True
            or not isinstance(value.issued_at,datetime) or not isinstance(value.expires_at,datetime)
            or value.issued_at.tzinfo is None or value.expires_at.tzinfo is None
            or not value.issued_at<=at<value.expires_at or not timedelta(0)<value.expires_at-value.issued_at<=timedelta(hours=7)
            or value.expires_at<_clock(record['due_at'])+timedelta(minutes=5)
            or not isinstance(value.authorization_reference,str) or not value.authorization_reference.strip()):
            raise PermissionError('没有覆盖该具体截止任务及后续核对的明确关停委托')
        return value

    def _ready(self,job_id,request_digest,at):
        self._check_enabled()
        with self._status_lock:
            recent=(self._last_monotonic is not None and time.monotonic()-self._last_monotonic<=max(10,self.interval*2+2)
                and self._last_tick is not None and 0<=(at-self._last_tick).total_seconds()<=max(10,self.interval*2+2)
                and self._problems.get('service') is None)
        if not self._thread or not self._thread.is_alive() or self._stop.is_set() or not recent:
            raise PermissionError('截止工作线程未运行或最近工作周期已过期')
        record=self.ledger.get(job_id)
        if (record['state']!='reserved_before_creation' or record['creation_request_digest']!=request_digest
            or record['control_type']!='material_chase' or not 0<=(at-_clock(record['reserved_at'])).total_seconds()<=60):
            raise PermissionError('截止预留已改变，不能发起该笔追投创建')
        try:cfg=self.settings_provider()
        except Exception:raise PermissionError('截止账户配置提供器未返回可用范围') from None
        accounts=registry_accounts(cfg)
        if len([a for a in accounts if a['id']==record['account_id'] and a['observe'] and a['scene']=='live'])!=1:
            raise PermissionError('截止工作账户范围已撤回或不再为直播')
        return record,self._delegation(record,at)

    def lease(self,job_id,request_digest):
        self._check_enabled()
        at=_clock(self.clock());record,_=self._ready(job_id,request_digest,at)
        return DeadlineLease(job_id,record['account_id'],request_digest,at,at+timedelta(seconds=30),time.monotonic(),self,_SEAL)

    def run_once(self):
        self._check_enabled()
        if self._stop.is_set():return {'stopped':True}
        if not self._tick_lock.acquire(blocking=False):return {'busy':True}
        results={};problems={}
        try:
            at=_clock(self.clock())
            with self.ledger.connect() as db:
                ids=[r[0] for r in db.execute('SELECT id FROM control_deadlines WHERE released_at IS NULL ORDER BY id')]
            for job_id in ids:
                if self._stop.is_set():break
                try:
                    record=self.ledger.get(job_id)
                    if record['state'] in TERMINAL or at<_clock(record['due_at']):continue
                    if record['native_creation_verified'] is not True or record['task_id'] is None:
                        problems[job_id]='creation_identity_unknown';continue
                    delegation=self._delegation(record,at,require_end=False);cfg=self.settings_provider()
                    preview=deadline_preview(record,cfg,at)
                    permit=DeadlinePermit(record['account_id'],preview['digest'],at,
                        min(at+timedelta(minutes=2),delegation.expires_at),delegation.authorization_reference,delegation.allow_end is True)
                    client=DeadlineClient(self.ledger,enable_network=True,clock=self.clock)
                    results[job_id]=client.step(job_id,cfg,permit,lambda:self.token_provider(copy.deepcopy(record)))
                    if results[job_id].get('state') in {'needs_independent_review','creation_identity_unknown'}:
                        problems[job_id]='independent_review_needed'
                except Exception:problems[job_id]='authorization_scope_or_read_not_ready'
            finished=_clock(self.clock())
            if finished<at:raise ValueError('截止工作时钟倒退')
            with self._status_lock:
                self._last_tick=finished;self._last_monotonic=time.monotonic();self._cycles+=1
                self._problems=problems;self._last_results=results
            return {'busy':False,'jobs_checked':len(ids),'results':copy.deepcopy(results),'problems':dict(problems)}
        finally:self._tick_lock.release()

    def _loop(self):
        try:
            while not self._stop.is_set():
                try:self.run_once()
                except Exception:
                    with self._status_lock:self._problems={'service':'cycle_not_complete'}
                self._started.set()
                self._stop.wait(self.interval)
        finally:self._started.set()

    def start(self):
        self._check_enabled()
        with self._lifecycle_lock:
            if self._thread and self._thread.is_alive():return self.status()
            self._stop.clear();self._started.clear()
            self._thread=threading.Thread(target=self._loop,name='qianchuan-native-deadline',daemon=True);self._thread.start()
        self._started.wait(1)
        return self.status()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():self._thread.join(timeout=2)
        return self.status()

    def status(self):
        with self._status_lock:
            return {'enabled':self.enabled,'thread_alive':bool(self._thread and self._thread.is_alive()),
                'stop_requested':self._stop.is_set(),'last_cycle_at':self._last_tick.isoformat() if self._last_tick else None,
                'cycles':self._cycles,'problems':dict(self._problems),'automatic_post_retry':False,
                'future_uptime_verified':False,'platform_metric_clock_verified':False}
