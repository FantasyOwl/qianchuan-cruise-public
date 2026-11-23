"""Local child-process recovery. Never controls Chrome or an account.

Only a Popen handle created here may be terminated. An occupied service port,
an HTTP timeout or a PID read from disk never authorizes terminating a process.
The OS lock is authoritative for a live manager; JSON is diagnostic state only.
"""
from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import threading
import time
import uuid

from .schema import now_cn
from .parent_guard import owner_environment

ROOT = Path(__file__).resolve().parent.parent


class AlreadyRunning(Exception):
    pass


class InstanceLock:
    def __init__(self,path):
        self.path=Path(path)
        self.file=None

    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        f=self.path.open('a+b')
        if f.seek(0,2)==0:
            f.write(b'0');f.flush()
        f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            f.close()
            if exc.errno in {11,13,35,36} or getattr(exc,'winerror',None) in {32,33,36}:
                raise AlreadyRunning from None
            raise
        self.file=f
        return self

    def release(self):
        if self.file:
            f,self.file=self.file,None
            try:
                f.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(f.fileno(),fcntl.LOCK_UN)
            finally:f.close()


def _read_json(path):
    try:
        with Path(path).open('rb') as f:raw=f.read(16385)
        if len(raw)>16384:return {}
        value=json.loads(raw)
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError,UnicodeError,RecursionError):return {}


def _write_json(path,value):
    path=Path(path);temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n','utf-8')
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def pause_background(root):
    """Explicit operator pause survives future scheduler triggers and logons."""
    data=Path(root)/'data';data.mkdir(parents=True,exist_ok=True)
    _write_json(data/'manual-stop.json',{'paused':True,'at':now_cn().isoformat()})


def allow_background(root):
    (Path(root)/'data/manual-stop.json').unlink(missing_ok=True)


def background_paused(root):
    # A malformed marker cannot silently authorize an unattended restart.
    return (Path(root)/'data/manual-stop.json').exists()


def _lock_held(root):
    path=Path(root)/'data'/'supervisor.lock'
    if not path.exists():return False
    lock=InstanceLock(path)
    try:lock.acquire()
    except AlreadyRunning:return True
    finally:lock.release()
    return False


def supervision_status(root,process_id=None):
    """Disk diagnostics plus an OS lock, never a PID-based kill decision."""
    root=Path(root);state=_read_json(root/'data/supervisor-state.json')
    try:held=_lock_held(root)
    except OSError:held=None
    fields=('run_id','manager_pid','child_pid','port','state','updated_at','started_at',
            'launch_count','restart_count','exits_observed','last_exit_code','restart_delay_seconds','error_type')
    result={k:state.get(k) for k in fields if k in state}
    result.update(manager_lock_held=held,diagnostic_state_only=True,
        background_paused=background_paused(root),
        current_process_supervised=bool(held is True and state.get('state')=='child_running'
            and type(state.get('child_pid')) is int and state['child_pid']==process_id),
        note='进程守护状态不证明接口可用、指标新鲜或7×24连续覆盖。')
    if not state:result['state']='not_managed'
    elif held is not True and state.get('state') not in {'stopped','port_in_use','stop_failed','supervisor_error'}:
        result['state']='manager_not_observed'
    return result


def request_stop(root,timeout=15):
    """Ask the lock-owning manager to stop its own child; never kill disk PIDs."""
    root=Path(root);state=supervision_status(root)
    if state['manager_lock_held'] is not True:
        return {'requested':False,'stopped':False,'state':'no_live_manager_observed'}
    run_id=state.get('run_id')
    if not isinstance(run_id,str) or not re.fullmatch(r'[0-9a-f]{32}',run_id):
        raise ValueError('守护器状态尚未就绪或无效，未提交停止请求')
    _write_json(root/'data/supervisor-stop.json',{'run_id':run_id})
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if not _lock_held(root):
            final=_read_json(root/'data/supervisor-state.json')
            stopped=final.get('run_id')==run_id and final.get('state')=='stopped'
            return {'requested':True,'stopped':stopped,'state':final.get('state','unknown')}
        time.sleep(.1)
    return {'requested':True,'stopped':False,'state':'stop_pending'}


def port_in_use(port):
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.settimeout(.2)
        return sock.connect_ex(('127.0.0.1',port))==0


class Supervisor:
    def __init__(self,root=ROOT,port=8877,stop_event=None,*,poll_seconds=1,restart_delays=(1,2,4,8,16,30,60),
                 worker_command=None, stop_exit_codes=()):
        if type(port) is not int or not 1024<=port<=65535:raise ValueError('端口范围必须为1024-65535')
        if not 0<poll_seconds<=5 or not restart_delays or any(not 0<v<=60 for v in restart_delays):
            raise ValueError('守护等待参数无效')
        self.root=Path(root).resolve();self.port=port;self.stop_event=stop_event or threading.Event()
        self.data=self.root/'data';self.child=None;self.console=None;self.run_id=uuid.uuid4().hex
        self.poll_seconds=poll_seconds;self.delays=tuple(restart_delays)
        self.worker_command=tuple(worker_command) if worker_command else None
        self.stop_exit_codes=frozenset(stop_exit_codes)
        self.launch_count=0;self.exits=0;self.last_exit=None;self.started_at=None;self.logger=None

    def _event(self,event,**fields):
        if self.logger:self.logger.info(json.dumps({'at':now_cn().isoformat(),'run_id':self.run_id,'event':event,**fields},ensure_ascii=False))

    def _state(self,state,**fields):
        _write_json(self.data/'supervisor-state.json',{'run_id':self.run_id,'manager_pid':os.getpid(),
            'child_pid':self.child.pid if self.child else None,'port':self.port,'state':state,
            'started_at':self.started_at,'updated_at':now_cn().isoformat(),'launch_count':self.launch_count,
            'restart_count':max(0,self.launch_count-1),'exits_observed':self.exits,'last_exit_code':self.last_exit,**fields})

    def _stop_requested(self):
        return self.stop_event.is_set() or background_paused(self.root) or _read_json(self.data/'supervisor-stop.json').get('run_id')==self.run_id

    def _wait(self,seconds):
        until=time.monotonic()+seconds
        while not self._stop_requested():
            remaining=until-time.monotonic()
            if remaining<=0:return False
            self.stop_event.wait(min(self.poll_seconds,remaining))
        return True

    def _spawn(self):
        # Only this fixed workspace entry is launched. No shell or URL command.
        path=self.data/'supervised-server-console.log'
        if path.exists() and path.stat().st_size>2*1024*1024:
            os.replace(path,path.with_suffix('.previous.log'))
        self.console=path.open('ab')
        try:
            env=os.environ.copy()
            # Never forward stale inherited identity from an outer supervisor.
            from .parent_guard import PID_ENV,TIME_ENV
            env.pop(PID_ENV,None);env.pop(TIME_ENV,None)
            env.update(owner_environment())
            command=list(self.worker_command) if self.worker_command else [sys.executable,'-u',str(self.root/'run.py')]
            self.child=subprocess.Popen([*command,'--port',str(self.port)],
                cwd=self.root,stdin=subprocess.DEVNULL,stdout=self.console,stderr=subprocess.STDOUT,
                env=env,close_fds=True,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except BaseException:
            self.console.close();self.console=None;raise
        self.launch_count+=1
        self._state('child_running');self._event('child_started',child_pid=self.child.pid,launch_count=self.launch_count)

    def _finish_child(self):
        try:
            if self.child and self.child.poll() is None:
                self._event('stopping_owned_child',child_pid=self.child.pid)
                self.child.terminate()
                try:self.child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.child.kill();self.child.wait(timeout=5)
        finally:
            if self.console:self.console.close();self.console=None

    def run(self):
        self.data.mkdir(parents=True,exist_ok=True)
        lock=InstanceLock(self.data/'supervisor.lock')
        try:lock.acquire()
        except AlreadyRunning:return 2
        terminal='stopped';code=0;streak=0
        self.started_at=now_cn().isoformat()
        try:
            self.logger=logging.getLogger('supervisor.'+self.run_id);self.logger.setLevel(logging.INFO);self.logger.propagate=False
            self.logger.addHandler(RotatingFileHandler(self.data/'supervisor-events.log',maxBytes=262144,backupCount=3,encoding='utf-8'))
            self._state('starting');self._event('manager_started')
            if not self.worker_command and not (self.root/'run.py').is_file():raise FileNotFoundError('run.py')
            while not self._stop_requested():
                if port_in_use(self.port):
                    terminal='port_in_use';code=2;self._event('port_in_use');break
                self._spawn();started=time.monotonic()
                while self.child.poll() is None:
                    if self._wait(self.poll_seconds):break
                if self._stop_requested():break
                # Only a terminal Popen result permits another launch.
                self.last_exit=self.child.poll()
                if self.last_exit is None:continue
                self.exits+=1;self._event('child_exited',child_pid=self.child.pid,exit_code=self.last_exit)
                if self.last_exit in self.stop_exit_codes:
                    terminal='worker_stopped';code=self.last_exit;break
                if time.monotonic()-started>=300:streak=0
                delay=self.delays[min(streak,len(self.delays)-1)];streak+=1
                self._finish_child();self.child=None
                self._state('backoff',restart_delay_seconds=delay)
                if self._wait(delay):break
        except KeyboardInterrupt:
            self.stop_event.set()
        except Exception as exc:
            terminal='supervisor_error';code=1;self._event('manager_error',error_type=type(exc).__name__)
        finally:
            try:
                try:self._finish_child()
                except Exception as exc:
                    terminal='stop_failed';code=1;self._event('stop_failed',error_type=type(exc).__name__)
                self._state(terminal);self._event('manager_finished',state=terminal)
            finally:
                if self.logger:
                    for handler in self.logger.handlers[:]:handler.close();self.logger.removeHandler(handler)
                lock.release()
        return code


def main():
    parser=argparse.ArgumentParser(description='本机只读助手进程守护；不注册开机任务，不操作账户')
    parser.add_argument('--port',type=int,default=8877)
    mode=parser.add_mutually_exclusive_group();mode.add_argument('--stop',action='store_true');mode.add_argument('--status',action='store_true')
    mode.add_argument('--scheduled',action='store_true');mode.add_argument('--allow-scheduled',action='store_true')
    args=parser.parse_args()
    if args.stop:
        pause_background(ROOT)
        result=request_stop(ROOT);print(json.dumps(result,ensure_ascii=True));return 0 if result.get('stopped') else 2
    if args.status:
        print(json.dumps(supervision_status(ROOT),ensure_ascii=True));return 0
    if args.allow_scheduled:
        allow_background(ROOT);print(json.dumps({'background_paused':False}));return 0
    if args.scheduled and background_paused(ROOT):
        print(json.dumps({'background_paused':True,'spawned':False}));return 0
    if not args.scheduled:
        allow_background(ROOT)
    print('Local read-only monitor supervisor. Ctrl+C or supervise.py --stop ends its owned child.',flush=True)
    return Supervisor(port=args.port).run()
