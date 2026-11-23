"""Account-scoped browser processes. This pool never grants business authority."""
from dataclasses import dataclass, field
import hmac
import json
import os
import re
import secrets
import subprocess
import time
from .runtime_files import ego_entry


@dataclass
class AccountWorker:
    account_id: str
    name: str
    directory: object
    worker_id: str = field(default_factory=lambda: 'ego:'+secrets.token_hex(16))
    epoch: str = field(default_factory=lambda: secrets.token_hex(16))
    token: str = field(default_factory=lambda: secrets.token_urlsafe(40))
    paused: bool = False
    suspended: bool = False
    resume_generation: int = 0
    process: object = None
    started: float = 0
    next_launch: float = 0
    last_launch: float = 0
    seen_at: object = None
    browser_seen: object = None
    busy: bool = False
    message: str = '等待本户网页任务'
    error: object = None
    space_id: object = None
    phase: object = None
    stages_started: int = 0
    last_stage_at: object = None
    last_completed_at: object = None
    control_wait: bool = False
    restart_requested: bool = False


class AccountPool:
    def __init__(self, executor):
        self.e=executor
        self.path=executor.app.root/'data/ego-account-executors.json'
        self.workers={}
        self.control_process=None;self.control_started=0;self.control_next=0;self.control_error=None
        self.settings={'mode':'serial','max_parallel':3,'accounts':{}}
        if self.path.exists():
            value=json.loads(self.path.read_text('utf-8'))
            if value.get('mode') not in {'serial','account_parallel'} or type(value.get('max_parallel')) is not int or not 1<=value['max_parallel']<=8 or not isinstance(value.get('accounts'),dict):
                raise ValueError('多户执行器配置损坏，未启动')
            for aid,control in value['accounts'].items():
                if not aid.isdigit() or set(control)!={'paused','suspended','resume_generation'} or any(type(control.get(k)) is not bool for k in ('paused','suspended')) or type(control['resume_generation']) is not int or control['resume_generation']<0:
                    raise ValueError('本户执行器配置损坏，未启动')
            self.settings=value

    @property
    def parallel(self):return self.settings['mode']=='account_parallel'

    def save(self):
        value={**self.settings,'accounts':{**self.settings['accounts'],**{aid:{k:getattr(w,k) for k in ('paused','suspended','resume_generation')} for aid,w in self.workers.items()}}}
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),'utf-8');temp.replace(self.path)
        self.settings=value

    def ensure(self, aid):
        # IDs determine directory names and credentials; never accept a path.
        account=self.e.a.account(aid);aid=account['id']
        if not isinstance(aid,str) or not aid.isascii() or not aid.isdigit() or not 6<=len(aid)<=30:raise ValueError('账户ID格式无效')
        with self.e.lock:
            if aid not in self.workers:
                directory=self.e.app.root/'data/ego-workers'/aid;directory.mkdir(parents=True,exist_ok=True)
                w=AccountWorker(aid,account.get('name') or account.get('tab_group') or aid,directory)
                for k,v in self.settings['accounts'].get(aid,{}).items():setattr(w,k,v)
                config={'token':w.token,'base':f'http://127.0.0.1:{self.e.app.port}','account_id':aid}
                (directory/'private.json').write_text(json.dumps(config,ensure_ascii=False),'utf-8')
                try:
                    runtime=json.loads((directory/'runtime.json').read_text('utf-8'))
                    if type(runtime.get('spaceId')) is int:w.space_id=runtime['spaceId']
                    last=next((r.get('message','') for r in reversed(runtime.get('trace',[])) if r.get('phase')=='failed'),'')
                    w.control_wait=w.suspended and bool(re.search(r'EGO_USER_CONTROL|Control of this task space is with the user',last))
                except (OSError,ValueError):pass
                self.workers[aid]=w
            return self.workers[aid]

    def credential(self, token):
        if not self.parallel or not isinstance(token,str):return None
        with self.e.lock:
            return next((aid for aid,w in self.workers.items() if hmac.compare_digest(w.token,token)),None)

    def configure(self,p):
        if not isinstance(p,dict) or set(p)!={'mode','max_parallel'} or p['mode'] not in {'serial','account_parallel'} or type(p['max_parallel']) is not int or not 1<=p['max_parallel']<=8:
            raise ValueError('请选择串行或按户并行，并发户数为1至8')
        with self.e.a.gate,self.e.lock:
            if p['mode']!=self.settings['mode'] and (self.e.process or any(w.process for w in self.workers.values())):
                raise ValueError('请先暂停常驻，等待当前阶段结束再切换架构')
            self.settings.update(p);self.save()
        return {'ok':True,'ad_changes':0,'executor':self.e.status()}

    def control(self,p):
        if not isinstance(p,dict) or set(p)!={'account_id','operation'} or p['operation'] not in {'pause','start','resume','restart'}:
            raise ValueError('请指定账户和暂停、启动或恢复控制')
        w=self.ensure(p['account_id'])
        if p['operation']=='restart':
            with self.e.a.gate:
                with self.e.lock:
                    w.paused=True;w.epoch=secrets.token_hex(16);w.resume_generation+=1
                    w.restart_requested=w.process is not None;w.suspended=False;w.control_wait=False
                    w.next_launch=0;w.last_launch=0;w.error=None;w.message='正在重启本户执行器；已提交原单继续API核验'
                    self.save()
                self.e.a.hybrid.reset_browser_worker(w.account_id,w.worker_id)
                with self.e.lock:w.paused=False;self.save()
            return {'ok':True,'ad_changes':0}
        with self.e.a.gate,self.e.lock:
            if p['operation']=='pause':
                w.paused=True;w.epoch=secrets.token_hex(16);w.message='本户执行已暂停；其他户继续'
            else:
                w.paused=False
                if p['operation']=='resume':w.suspended=False;w.resume_generation+=1
                w.next_launch=0;w.last_launch=0
                w.message='等待本户原规则；浏览器控制权待恢复' if w.suspended else '本户执行已启动，等待原规则任务'
            self.save()
        return {'ok':True,'ad_changes':0}

    def event(self,w,p):
        if set(p)-{'message','error','suspended','busy','connected','space_id','phase'}:raise ValueError('未知 Ego 状态字段')
        with self.e.lock:
            w.seen_at=self.e.a.clock();self.e.last_seen=w.seen_at
            w.message=str(p.get('message',''))[:300];w.busy=p.get('busy') is True
            if 'error' in p:w.error=str(p['error'])[:400] if p.get('error') else None
            if p.get('connected') is True:w.browser_seen=self.e.a.clock()
            if type(p.get('space_id')) is int:w.space_id=p['space_id']
            if p.get('phase') is not None:w.phase=str(p['phase'])[:40]
            if 'error' in p and p['error'] is None and p.get('connected') is True and not p.get('busy'):w.last_completed_at=w.seen_at.isoformat()
            if p.get('suspended') is True:
                w.suspended=True;w.control_wait=bool(re.search(r'EGO_USER_CONTROL|Control of this task space is with the user',w.error or w.message));self.save()
        return {'ok':True,'ad_changes':0}

    def public(self):
        now=self.e.a.clock()
        with self.e.lock:
            return [{ 'account_id':w.account_id,'name':w.name,'paused':w.paused,'suspended':w.suspended,
                'current':bool(w.browser_seen and 0<=(now-w.browser_seen).total_seconds()<=120),
                'running':bool(w.process),'busy':w.busy,'seen_at':w.seen_at.isoformat() if w.seen_at else None,
                'message':w.message,'error':w.error,'space_id':w.space_id,'phase':w.phase,
                'stages_started':w.stages_started,'last_stage_at':w.last_stage_at,'last_completed_at':w.last_completed_at,
                'automatic_control_return':w.control_wait and w.suspended and not w.paused,
                'resume_generation':w.resume_generation} for w in self.workers.values()]

    def apply_ownership(self,spaces):
        """Continue only a Space already handed back to the agent in Ego.

        An ownership observation never grants a claim/takeover, changes the
        resume generation, arms execution, or revokes an operator pause.
        """
        if not isinstance(spaces,list) or any(not isinstance(s,dict) or type(s.get('id')) is not int or not isinstance(s.get('name'),str) or not isinstance(s.get('ownership'),str) for s in spaces):
            raise ValueError('Ego控制权状态格式无效')
        resumed=[]
        with self.e.lock:
            for w in self.workers.values():
                matching=[s for s in spaces if s['id']==w.space_id and s['name']=='千川巡航 · '+w.account_id]
                if not w.suspended or w.paused or not w.control_wait or len(matching)!=1 or matching[0]['ownership']!='agent':continue
                w.suspended=False;w.control_wait=False;w.epoch=secrets.token_hex(16)
                w.message='检测到Ego已归还智能体控制，继续本户原规则';w.error=None;w.next_launch=0
                resumed.append(w.account_id)
            if resumed:self.save()
        return resumed

    def poll_control(self,stop_event):
        proc=self.control_process
        if proc is None:return
        expired=time.monotonic()-self.control_started>8
        if proc.poll() is None and not stop_event.is_set() and not expired:return
        if proc.poll() is None:proc.terminate()
        try:output,error=proc.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill();output,error=proc.communicate(timeout=2)
        self.control_process=None;self.control_next=time.monotonic()+10
        if stop_event.is_set():return
        try:
            if proc.returncode or expired:raise ValueError('Ego控制权读取暂不可用')
            # Windows Ego writes console output to stderr; other versions use
            # stdout. Require one exact protocol record across both streams.
            lines=[line[len('QCX_OWNERSHIP_V1:'):] for stream in (output,error) for line in stream.decode('utf-8').splitlines() if line.startswith('QCX_OWNERSHIP_V1:')]
            if len(lines)!=1:raise ValueError('Ego控制权回传不完整')
            self.apply_ownership(json.loads(lines[0]));self.control_error=None
        except (ValueError,UnicodeError) as exc:self.control_error=str(exc)

    def watch_control(self,stop_event):
        self.poll_control(stop_event)
        if self.control_process or time.monotonic()<self.control_next or not any(w.suspended and w.control_wait and not w.paused for w in self.workers.values()):return
        try:
            entry=ego_entry(self.e.app, 'control-watch.mjs')
            self.control_process=subprocess.Popen([str(self.e.exe),'nodejs','-e',entry],cwd=str(self.e.app.root),stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            self.control_started=time.monotonic()
        except OSError as exc:self.control_error=type(exc).__name__;self.control_next=time.monotonic()+10

    def discover(self,hints):
        ids={r['account_id'] for r in hints}
        for a in self.e.a.accounts():
            if not a.get('observe',True):continue
            if any((getattr(self.e.a,name).row(a['id']) or {}).get('enabled') for name in ('auto','rule_auto')):ids.add(a['id'])
        for aid in ids:
            try:self.ensure(aid)
            except ValueError:continue  # a removed/unobserved account cannot gain a worker

    def _launch(self,w,now):
        config=w.directory/'private.json'
        entry=ego_entry(self.e.app, 'worker.mjs', config)
        w.process=subprocess.Popen([str(self.e.exe),'nodejs','-e',entry],cwd=str(self.e.app.root),stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        w.started=w.last_launch=now
        w.stages_started+=1;w.last_stage_at=self.e.now()

    def poll(self,stop_event):
        now=time.monotonic()
        # Only this service's CLI children are stopped, never the browser.
        for w in list(self.workers.values()):
            proc=w.process
            if proc is None:continue
            timed_out=now-w.started>180
            restarting=w.restart_requested
            if proc.poll() is None and not stop_event.is_set() and not timed_out and not restarting:continue
            if proc.poll() is None:proc.terminate()
            try:output,error=proc.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill();output,error=proc.communicate(timeout=3)
            with self.e.lock:
                if restarting:w.error=None;w.message='旧脚本已结束，正在立即重启本户';w.restart_requested=False
                elif timed_out:w.error='本户网页阶段超过3分钟，已结束本机脚本；原提交只回读';w.message=w.error
                elif proc.returncode and not stop_event.is_set():w.error='本户执行脚本退出，原单保留；'+error.decode('utf-8','replace')[-200:]
                w.process=None;w.busy=False;w.next_launch=0 if restarting else now+(30 if proc.returncode or timed_out else 3)

    def tick(self,stop_event):
        self.poll(stop_event)
        if stop_event.is_set() or not self.e.selected() or not self.e.settings['enabled'] or self.e.suspended:return
        hints=self.e.queue_hints(self.e.metadata());self.discover(hints)
        self.watch_control(stop_event)
        now=time.monotonic()
        ready={r['account_id'] for r in hints if r['ready']}
        active=sum(w.process is not None for w in self.workers.values())
        candidates=[w for w in self.workers.values() if not w.process and not w.paused and not w.suspended and w.next_launch<=now and (w.account_id in ready or now-w.last_launch>=30)]
        candidates.sort(key=lambda w:(w.account_id not in ready,w.last_launch,w.account_id))
        with self.e.lock:
            self.e.queue=self.e.scheduler.order(hints)+[r for r in hints if not r['ready']]
            if not self.parallel or not self.e.selected() or not self.e.settings['enabled'] or self.e.suspended or stop_event.is_set():return
            for w in candidates[:max(0,self.settings['max_parallel']-active)]:
                if w.paused or w.suspended or w.process is not None:continue
                try:self._launch(w,now)
                except OSError as exc:w.error='本户执行器暂不可用：'+type(exc).__name__;w.next_launch=now+30

    def shutdown(self,stop_event):
        stop_event.set();self.poll(stop_event);self.poll_control(stop_event)
