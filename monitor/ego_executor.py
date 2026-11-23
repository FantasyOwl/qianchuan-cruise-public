"""Local Ego executor supervisor; business authority remains in the action ledger."""
from __future__ import annotations

import contextlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
import time
from .ego_scheduler import FairAccounts
from .schema import parse_time
from .execution_strategy import api_control
from .runtime_files import ego_entry

VERSION = '1.23.4'


class EgoExecutor:
    def __init__(self, app):
        self.app = app
        self.a = app.official_actions
        self.path = app.root/'data/ego-executor.json'
        self.private_path = app.root/'data/ego-executor-private.json'
        self.lock = threading.RLock()
        self.context = threading.local()
        self.process = None
        self.thread = None
        self.last_seen = None
        self.browser_seen = None
        self.last_message = 'Ego 常驻尚未启动'
        self.last_error = None
        self.busy = False
        self.suspended = False
        self.stopping = False
        self.resume_generation = 0
        self.last_kind = 'rules'
        self.scheduler=FairAccounts(app.root/'data/ego-scheduler.json')
        self.queue=[]
        self.settings = {'provider':'extension', 'enabled':False, 'armed':False}
        if self.path.exists():
            loaded = json.loads(self.path.read_text('utf-8'))
            if loaded.get('provider') in {'extension','ego'}:
                self.settings.update({k:loaded[k] for k in self.settings if k in loaded})
        if any(type(self.settings[k]) is not bool for k in ('enabled','armed')):
            raise ValueError('Ego 执行器设置损坏，未启动')
        self.secret = secrets.token_urlsafe(40)
        self.worker_id = 'ego:'+secrets.token_hex(16)
        self.epoch = secrets.token_hex(16)
        self.exe = Path.home()/'.local/share/ego/active_version_dir/ego-browser.exe'
        self.private_path.write_text(json.dumps({'token':self.secret, 'base':f'http://127.0.0.1:{app.port}'},ensure_ascii=False), 'utf-8')
        from .ego_pool import AccountPool
        self.pool=AccountPool(self)

    def now(self):
        return self.a.clock().isoformat()

    def selected(self):
        return self.settings['provider'] == 'ego'

    def authenticate(self, token):
        return self.credential(token) is not None

    def credential(self,token):
        if self.pool.parallel:return self.pool.credential(token)
        return 'serial' if isinstance(token,str) and hmac.compare_digest(token,self.secret) else None

    def session(self):
        aid=getattr(self.context,'account_id',None)
        return self.pool.workers[aid] if aid is not None else self

    def session_armed(self):
        w=self.session()
        return self.settings['armed'] and not w.suspended and not getattr(w,'paused',False)

    def status(self):
        with self.lock:
            workers=self.pool.public() if self.pool.parallel else []
            current = any(w['current'] for w in workers) if self.pool.parallel else bool(self.browser_seen and 0 <= (self.a.clock()-self.browser_seen).total_seconds() <= 120)
            paused={w['account_id']:w for w in workers if w['paused'] or w['suspended']}
            queue=[{**r,'ready':False,'reason':'本户已暂停或浏览器控制权待恢复'} if r['account_id'] in paused else {**r,'ready':False,'reason':'浏览器控制权待恢复，网页执行暂停'} if self.suspended else dict(r) for r in self.queue]
            return {**self.settings, 'installed':self.exe.is_file(), 'version':VERSION,
                    'running':bool(self.thread and self.thread.is_alive() and self.settings['enabled']),
                    'current':current, 'seen_at':self.last_seen.isoformat() if self.last_seen else None,
                    'busy':any(w['running'] for w in workers) if self.pool.parallel else self.busy, 'suspended':self.suspended,
                    'message':('按户独立执行 · '+str(sum(w['running'] for w in workers))+'户正在处理 · '+str(sum(w['paused'] or w['suspended'] for w in workers))+'户暂停') if self.pool.parallel else self.last_message,
                    'error':self.last_error,
                    'control_return_error':self.pool.control_error if self.pool.parallel else None,
                    'resume_generation':self.resume_generation,
                    'mode':self.pool.settings['mode'],'max_parallel':self.pool.settings['max_parallel'],'account_workers':workers,
                    'scheduler':{'policy':'account_parallel' if self.pool.parallel else 'account_round_robin','quantum':'one_browser_stage','last_account_id':self.scheduler.last_account,'queue':queue}}

    def queue_hints(self,meta):
        rows=[];now=self.a.clock()
        with self.a.db() as db:
            tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for kind,table in (('rules','hybrid_rule_runs'),('chase','hybrid_runs')):
                if api_control(self.a) and kind=='rules':continue
                if table not in tables:continue
                for r in db.execute(f"SELECT * FROM {table} WHERE state NOT IN ('completed','cancelled')"):
                    d=json.loads(r['data']);phase=d.get('phase','')
                    if api_control(self.a) and phase not in {'prepare','dry_prepare'}:continue
                    reason=''
                    if d.get('paused') or d.get('auto_retry_paused'):reason='本阶段暂停，请查看失败原因'
                    elif d.get('yielding'):reason='等待本户规则原单结束'
                    elif phase=='api_materials':reason='API正在取数'
                    elif d.get('retry_at') and parse_time(d['retry_at'])>now:reason='等待本阶段重试时间'
                    elif not meta['armed'] and not d.get('preview') and phase!='verify':reason='网页执行关闭'
                    priority=0 if phase=='verify' else 1 if kind=='rules' and phase=='prepare' and self._is_pause(db,d.get('action_id')) else 2
                    rows.append({'account_id':r['account_id'],'kind':kind,'phase':phase,'priority':priority,'ready':not reason,
                        'reason':reason,'updated_at':r['updated_at']})
        return rows

    @staticmethod
    def _is_pause(db,action_id):
        if not action_id:return False
        r=db.execute('SELECT intent FROM api_actions WHERE id=?',(action_id,)).fetchone()
        return bool(r and json.loads(r['intent']).get('action')=='pause_task')

    def bridge(self,account_id=None):
        s = self.status()
        if self.pool.parallel:
            w=next((w for w in s['account_workers'] if w['account_id']==account_id),None)
            return {'kind':'ego','version':VERSION,'armed':bool(w and s['armed'] and s['enabled'] and not w['paused'] and not w['suspended']),
                    'seen_at':w['seen_at'] if w else None,'current':bool(w and w['current'] and s['enabled'] and not w['paused'] and not w['suspended'])}
        return {'kind':'ego', 'version':VERSION, 'armed':s['armed'] and s['enabled'] and not s['suspended'],
                'seen_at':s['seen_at'], 'current':s['current'] and s['enabled'] and not s['suspended']}

    def configure(self, p):
        if not isinstance(p,dict) or set(p) != {'provider','enabled','armed'} or p['provider'] not in {'ego','extension'} or any(type(p[k]) is not bool for k in ('enabled','armed')):
            raise ValueError('请指定执行器、常驻和网页执行状态')
        if p['armed'] and (p['provider'] != 'ego' or not p['enabled']):
            raise ValueError('请先启动 Ego 常驻，再开启网页执行')
        if p['provider'] == 'ego' and not self.exe.is_file():
            raise ValueError('尚未找到本机 Ego Lite，请完成安装')
        with self.a.gate,self.lock:
            if p['provider'] != self.settings['provider'] and self.busy:
                raise ValueError('当前浏览器阶段仍在回传，请先关闭执行并等待本阶段结束后切换')
            if self.pool.parallel and (p['armed']!=self.settings['armed'] or not p['enabled']):
                for w in self.pool.workers.values():w.epoch=secrets.token_hex(16)
            self.settings = dict(p)
            self.path.write_text(json.dumps(self.settings,ensure_ascii=False,indent=2), 'utf-8')
            self.last_message = 'Ego 常驻已启动；'+('沿用本户原授权执行' if p['armed'] else '网页执行关闭，可读取和预演') if p['enabled'] else 'Ego 常驻已暂停'
        return {'ok':True, 'ad_changes':0, 'executor':self.status()}

    def resume(self,p):
        if p != {}:
            raise ValueError('恢复控制不接受额外参数')
        with self.lock:
            self.suspended = False
            self.resume_generation += 1
            self.last_error = None
            self.last_message = '用户已恢复 Ego 执行控制；等待重新核对页面'
        return {'ok':True,'ad_changes':0}

    def guard_worker(self,p):
        kind = getattr(self.context,'kind','extension')
        if kind == 'ego':
            if not self.selected() or not self.settings['enabled'] or self.suspended or self.stopping:
                raise ValueError('Ego 常驻已停止或控制权已交给用户')
            session=self.session()
            if self.pool.parallel and session is self:raise ValueError('并行模式必须使用本户执行连接')
            if session.suspended or getattr(session,'paused',False):raise ValueError('本户执行已暂停或控制权已交给用户')
            if p['worker_id'] != session.worker_id or p['epoch'] != session.epoch or p['armed'] != self.session_armed():
                raise ValueError('Ego 执行会话已变化')
        elif self.selected():
            raise ValueError('网页任务已迁移到 Ego Lite，原扩展不再领取执行任务')

    def legacy_allowed(self):
        return not self.selected()

    @contextlib.contextmanager
    def ego_context(self,account_id=None):
        old_kind=getattr(self.context,'kind','extension');old_aid=getattr(self.context,'account_id',None)
        self.context.kind = 'ego'
        if account_id is not None:self.context.account_id=account_id
        try:
            yield
        finally:
            self.context.kind=old_kind;self.context.account_id=old_aid

    def metadata(self):
        session=self.session()
        return {'protocol':1,'worker_id':session.worker_id,'epoch':session.epoch,
                'armed':self.session_armed(),'revision':self.a.hybrid.policy()['revision'], 'extension_version':VERSION}

    def check(self,p):
        if not self.selected() or not self.settings['enabled'] or self.suspended or self.stopping:
            raise ValueError('Ego 执行器已暂停或切换')
        if p.get('kind') not in {'chase','rules'}:
            raise ValueError('未知网页任务类型')
        body=p.get('body')
        if not isinstance(body,dict):
            raise ValueError('缺少原网页任务')
        table='hybrid_runs' if p['kind']=='chase' else 'hybrid_rule_runs'
        with self.a.db() as db:
            row=db.execute(f'SELECT account_id,data FROM {table} WHERE id=?',(body.get('run_id'),)).fetchone()
        if not row:
            raise ValueError('原网页轮次不存在')
        d=json.loads(row['data'])
        session=self.session()
        if self.pool.parallel and (session is self or row['account_id']!=session.account_id or session.paused or session.suspended):
            raise ValueError('原网页轮次不属于当前账户执行器，或本户已暂停')
        command=d.get('command') or {}
        if 'submit_token' in body:
            permit=d.get('permit') or {}
            if permit.get('ego_worker_id') != session.worker_id or permit.get('ego_epoch') != session.epoch:
                raise ValueError('最终许可不是当前 Ego 会话')
        elif command.get('worker_id') != session.worker_id or command.get('epoch') != session.epoch:
            permit=d.get('permit') or {}
            from .schema import parse_time
            if not (permit.get('ego_worker_id')==session.worker_id and permit.get('ego_epoch')==session.epoch
                    and body.get('id')==permit.get('ego_command_id') and hmac.compare_digest(str(body.get('lease','')),permit.get('ego_lease',''))
                    and self.a.clock()<parse_time(permit['expires_at'])):
                raise ValueError('原命令不是当前 Ego 会话')
        return d

    def dispatch(self, operation,p,*,worker=None):
        if worker is not None and worker!='serial':
            if not self.pool.parallel or worker not in self.pool.workers:raise ValueError('本户执行连接已失效')
            with self.ego_context(worker):return self._dispatch(operation,p)
        if self.pool.parallel:raise ValueError('并行模式不接受全局执行凭据')
        return self._dispatch(operation,p)

    def editor_proof(self, body):
        if set(body)!={'run_id','id','lease','task_name'}:raise ValueError('旧窗口核对字段无效')
        name=body['task_name']
        match=re.fullmatch(r'QCX_\d{4}_([0-9a-f]{16})',name) if isinstance(name,str) else None
        if not match:return {'ok':True,'editor':None}
        prefix=match[1]
        with self.a.db() as db:
            run,_=self.a.hybrid.bound(db,body)
            rows=[(r,json.loads(r['intent'])) for r in db.execute('SELECT * FROM api_actions WHERE id BETWEEN ? AND ?',
                (prefix+'0'*8,prefix+'f'*8))]
            matches=[(r,i) for r,i in rows if i.get('account_id')==run['account_id'] and i.get('plan_id')==run['plan_id']
                and i.get('action')=='material_chase' and i.get('execution_channel')=='browser'
                and i.get('browser_batch',{}).get('task_name')==name]
            if len(matches)!=1:return {'ok':True,'editor':None}
            row,intent=matches[0]
            closed=row['state'] in {'cancelled','expired','rejected','verified'}
            if row['state']=='unknown':
                for old in db.execute("SELECT data FROM hybrid_runs WHERE account_id=? AND plan_id=? AND state='waiting' AND json_extract(data,'$.action_id')=?",
                        (run['account_id'],run['plan_id'],row['id'])):
                    data=json.loads(old['data']);permit=data.get('permit') or {}
                    if (data.get('phase')=='verify' and data.get('isolated_readback') is True and data.get('stop_requested') is True
                            and permit.get('action_id')==row['id'] and permit.get('consumed') is True
                            and self.a.clock()>=parse_time(permit['expires_at'])):
                        closed=True;break
            if not closed:return {'ok':True,'editor':None}
            batch=intent['browser_batch']
            return {'ok':True,'editor':{'action_id':row['id'],'account_id':run['account_id'],'plan_id':run['plan_id'],
                'task_name':name,'material_ids':batch['material_ids'],'after':batch['after']}}

    def _dispatch(self, operation,p):
        if not isinstance(p,dict):
            raise ValueError('Ego 请求格式无效')
        if api_control(self.a) and p.get('kind')=='rules':raise ValueError('智能体仅用于新建追投，其他调控由API执行')
        with self.lock:
            self.last_seen=self.a.clock()
        session=self.session();account_id=getattr(session,'account_id',None)
        if operation == 'event':
            if account_id is not None:return self.pool.event(session,p)
            if set(p)-{'message','error','suspended','busy','connected'}:
                raise ValueError('未知 Ego 状态字段')
            with self.lock:
                self.last_message=str(p.get('message',''))[:300]
                if 'error' in p:self.last_error=str(p['error'])[:400] if p.get('error') else None
                self.busy=p.get('busy') is True
                if p.get('connected') is True:self.browser_seen=self.a.clock()
                if p.get('suspended') is True:
                    self.suspended=True
            return {'ok':True, 'ad_changes':0}
        if operation == 'next':
            if p:
                raise ValueError('领取任务不接受投放参数')
            if not self.selected() or not self.settings['enabled'] or self.suspended or session.suspended or getattr(session,'paused',False):
                return {'ok':True,'command':None,'paused':True}
            with self.ego_context():
                meta=self.metadata()
                hints=self.queue_hints(meta)
                if account_id is not None:hints=[r for r in hints if r['account_id']==account_id]
                with self.lock:
                    if account_id is None:self.queue=self.scheduler.order(hints)+[r for r in hints if not r['ready']]
                    turns=self.scheduler.order(hints)
                reply={'ok':True,'command':None};kind=None
                for turn in turns:
                    handler=self.a.hybrid_rules if turn['kind']=='rules' else self.a.hybrid
                    reply=handler.next(meta,account_id=turn['account_id'])
                    if reply.get('command'):
                        kind=turn['kind']
                        with self.lock:self.scheduler.mark(turn['account_id'])
                        break
                if not hints and account_id is None:
                    # Compatibility for idle/legacy diagnostic adapters. Real
                    # active runs always use the scoped account scheduler.
                    urgent=getattr(self.a.hybrid_rules,'urgent',None)
                    reply=urgent(meta) if urgent else {'command':None}
                    if reply.get('command'):kind='rules';self.last_kind=kind
                if not hints and account_id is None and not reply.get('command'):
                    order=('chase','rules') if self.last_kind=='rules' else ('rules','chase')
                    for kind in order:
                        handler=self.a.hybrid if kind=='chase' else self.a.hybrid_rules
                        reply=handler.next(meta)
                        if reply.get('command'):
                            self.last_kind=kind
                            break
            c=reply.get('command')
            if c and account_id is not None and c.get('account_id')!=account_id:raise ValueError('网页命令跨户，未交给浏览器')
            if c and c.get('account_id') and c.get('plan_id'):
                # Route from this exact parent's cached API family. This is
                # navigation evidence only; native identity/final checks remain.
                plans=[r for r in getattr(self.a.api,'plan_readings',{}).get(c['account_id'],{}).get('plans',[]) if r.get('plan_id')==c['plan_id']]
                if len(plans)==1 and plans[0].get('delivery_entry') in {'uni_promotion','chengfang'}:c['delivery_entry']=plans[0]['delivery_entry']
            if c and c.get('account_id') and c.get('plan_id'):
                with self.a.db() as db:
                    c['cleanup_action_ids']=[r['id'] for r in db.execute("SELECT id FROM api_actions WHERE state IN ('cancelled','expired','rejected') AND json_extract(intent,'$.account_id')=? AND json_extract(intent,'$.plan_id')=? ORDER BY created_at DESC LIMIT 50",(c['account_id'],c['plan_id']))]
            return {**reply,'kind':kind if c else None,'resume_generation':session.resume_generation}
        if operation not in {'checkpoint','result','begin','final','editor-proof'} or set(p) != {'kind','body'}:
            raise ValueError('未知 Ego 操作')
        # The business gate already serializes configure/begin/final. Keep the
        # status/event mutex free while the handler performs network preflight.
        with self.a.gate:
            data=self.check(p)
            if operation=='editor-proof':
                if p['kind']!='chase':raise ValueError('仅核对素材追投旧窗口')
                return self.editor_proof(p['body'])
            handler=self.a.hybrid if p['kind']=='chase' else self.a.hybrid_rules
            if operation in {'begin','final'} and not self.session_armed():
                raise ValueError('Ego 网页执行已关闭；未授权点击')
            if operation == 'checkpoint':
                session.browser_seen=self.a.clock()
                if data.get('command'):
                    with self.a.db() as db:
                        handler.bound(db,p['body'])
                with self.ego_context():
                    self.a.hybrid.worker(self.metadata())
                return {'ok':True}
            with self.ego_context():
                answer=getattr(handler,operation)(p['body'])
            if operation == 'begin' and answer.get('submit_once'):
                table='hybrid_runs' if p['kind']=='chase' else 'hybrid_rule_runs'
                with self.a.db() as db:
                    row=db.execute(f'SELECT data FROM {table} WHERE id=?',(p['body']['run_id'],)).fetchone()
                    d=json.loads(row['data']);d['permit'].update(ego_worker_id=session.worker_id,ego_epoch=session.epoch,
                        ego_command_id=p['body']['id'],ego_lease=p['body']['lease'])
                    db.execute(f'UPDATE {table} SET data=? WHERE id=?',(json.dumps(d,ensure_ascii=False),p['body']['run_id']))
            return answer

    def supervise(self, stop_event):
        self.stopping=False
        while not stop_event.is_set():
            if self.pool.parallel:
                try:self.pool.tick(stop_event)
                except Exception as exc:self.last_error='多户执行调度暂不可用：'+type(exc).__name__
                stop_event.wait(.5);continue
            if not self.selected() or not self.settings['enabled'] or self.suspended:
                stop_event.wait(2);continue
            try:
                entry=ego_entry(self.app, 'worker.mjs')
                with self.lock:
                    self.process=subprocess.Popen([str(self.exe),'nodejs','-e',entry],cwd=str(self.app.root),
                        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
                deadline=time.monotonic()+180
                while self.process.poll() is None and not stop_event.wait(.5) and time.monotonic()<deadline:
                    pass
                if stop_event.is_set():
                    self.stopping=True
                    self.process.terminate()  # only our CLI child, never the browser
                elif self.process.poll() is None:
                    self.process.terminate()
                    self.last_error='Ego 本轮超过3分钟，已停止本机脚本；原提交只回读，不重发'
                output,error=self.process.communicate(timeout=8)
                if self.process.returncode and not stop_event.is_set():
                    self.last_error='Ego 执行轮次退出，原单保留；'+error.decode('utf-8','replace')[-240:]
                self.process=None
                self.busy=False
            except Exception as exc:
                self.last_error='Ego 常驻连接暂不可用：'+type(exc).__name__
            stop_event.wait(3)
        self.stopping=True
        self.pool.shutdown(stop_event)

    def start(self,stop_event):
        if not self.thread or not self.thread.is_alive():
            self.thread=threading.Thread(target=self.supervise,args=(stop_event,),daemon=True,name='ego-resident-executor')
            self.thread.start()
