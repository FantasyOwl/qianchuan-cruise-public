"""F5 browser fallback using the official action ledger and existing authority.

Only normal page controls are used by the extension; API calls are read-only.
A submission permit is persisted before the browser clicks;
an interrupted permit is an unknown write, never an invitation to create again.
"""
import copy
import json
import re
import secrets
import threading
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlparse, parse_qs

from .actuation import amount
from .batch_chase import date_range, matches
from .official_api import exact_id
from .official_workflow import digest
from .schema import parse_time
from .execution_strategy import api_control

MODES={'api_only','api_preferred','browser_only','api_data_browser'}
PENDING=('draft','checking','submitting','unknown','submitted_pending_verification')
TERMINAL={'已暂停','调控暂停','调控结束','已结束','已删除','调控失败'}
PHASE_LABELS={'tasks':'核对在投任务及预算','lookup':'核对历史任务状态','materials':'扫描近几天素材','api_materials':'API读取近几天素材',
    'api_inventory':'API读取在投任务与预算',
    'dry_prepare':'预演追投表单并取消','prepare':'核对本批素材与追投表单','verify':'只读核验原提交结果','dom_probe':'只读诊断页面结构'}

def current_writer(version):
    try:return tuple(int(x) for x in version.split('.'))>=(1,8,0) and len(version.split('.'))==3
    except (AttributeError,ValueError):return False


def api_material_writer(version):
    try:return len(version.split('.'))==3 and tuple(int(x) for x in version.split('.'))>=(1,10,0)
    except (AttributeError,ValueError):return False

def precise_selection_reader(version):
    try:return len(version.split('.'))==3 and tuple(int(x) for x in version.split('.'))>=(1,10,6)
    except (AttributeError,ValueError):return False

def home_entry_writer(version):
    try:return len(version.split('.'))==3 and tuple(int(x) for x in version.split('.'))>=(1,11,0)
    except (AttributeError,ValueError):return False

def parent_anchor_writer(version):
    try:return len(version.split('.'))==3 and tuple(int(x) for x in version.split('.'))>=(1,11,2)
    except (AttributeError,ValueError):return False


def direct_home_writer(command):
    try:return str(command.get('worker_id','')).startswith('ego:') and tuple(int(x) for x in command['extension_version'].split('.'))>=(1,14,0)
    except (KeyError,AttributeError,ValueError):return False

def dom_probe_reader(version):
    try:return len(version.split('.'))==3 and tuple(int(x) for x in version.split('.'))>=(1,11,3)
    except (AttributeError,ValueError):return False


def metric(value):
    if isinstance(value,bool) or not isinstance(value,(int,float,str,Decimal)):raise ValueError('素材指标无效')
    try:n=Decimal(str(value))
    except Exception:raise ValueError('素材指标无效') from None
    if not n.is_finite() or not 0<=n<=10**12:raise ValueError('素材指标无效')
    return n


class HybridChase:
    def __init__(self,actions,policy):
        self.a=actions
        self.policy=policy
        self.lock=threading.RLock()
        self.api_scan_thread=None
        from .api_chase_lane import APIChaseLane
        self.api_lane=APIChaseLane(self)
        with self.a.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS hybrid_policy(account_id TEXT PRIMARY KEY,mode TEXT,updated_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS hybrid_runs(id TEXT PRIMARY KEY,account_id TEXT,plan_id TEXT,state TEXT,updated_at TEXT,data TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS hybrid_clients(worker_id TEXT PRIMARY KEY,version TEXT,armed INTEGER,seen_at TEXT)')
            db.execute("UPDATE hybrid_runs SET state='waiting',updated_at=? WHERE state='working'",(self.now(),))

    def now(self):return self.a.clock().isoformat()

    def executor_name(self):
        executor=getattr(self.a,'browser_executor',None)
        return 'Ego Lite' if executor and executor.selected() else '原Chrome'

    def mode(self,aid):
        with self.a.db() as db:r=db.execute('SELECT mode FROM hybrid_policy WHERE account_id=?',(aid,)).fetchone()
        return r[0] if r else 'api_only'

    def parent_anchor(self,run):
        reading=self.a.api.plan_readings.get(run['account_id'],{})
        rows=[p for p in reading.get('plans',[]) if p.get('plan_id')==run['plan_id']]
        if len(rows)!=1:return None
        p=rows[0]
        try:
            anchors=p.get('anchors',[])
            if p.get('account_id')!=run['account_id'] or p.get('configuration_source')!='plan_list' or len(anchors)!=1:return None
            uid=exact_id(anchors[0]['id']);at=p['captured_at']
            if not 0<=(self.a.clock()-parse_time(at)).total_seconds()<=600:return None
            related=[q for q in reading.get('plans',[]) if any(a.get('id')==uid for a in q.get('anchors',[]))]
            if len(related)!=1:return None
        except (ValueError,TypeError,KeyError):return None
        return {'kind':'unique_official_plan_anchor','account_id':run['account_id'],'plan_id':run['plan_id'],'aweme_id':uid,'captured_at':at}

    def status(self):
        with self.a.db() as db:
            policies={r['account_id']:r['mode'] for r in db.execute('SELECT * FROM hybrid_policy')}
            runs=[dict(r) for r in db.execute('SELECT * FROM hybrid_runs ORDER BY updated_at DESC')]
            client=db.execute('SELECT version,armed,seen_at FROM hybrid_clients ORDER BY seen_at DESC LIMIT 1').fetchone()
            bridge=dict(client) if client else None
            if bridge:bridge.update(armed=bool(bridge['armed']),current=0<=(self.a.clock()-parse_time(bridge['seen_at'])).total_seconds()<=120)
        executor=getattr(self.a,'browser_executor',None)
        out=[]
        for account in self.a.accounts():
            if executor and executor.selected():bridge=executor.bridge(account['id'])
            scoped=[r for r in runs if r['account_id']==account['id']]
            run=next((r for r in scoped if not json.loads(r['data']).get('isolated_readback')),scoped[0] if scoped else None)
            data=json.loads(run['data']) if run else {}
            from .chase_recovery import reservations
            with self.a.db() as db:
                isolated=[{'action_id':r['action_id'],'reserved_budget':str(r['budget']),'material_ids':sorted(r['material_ids']),
                           'message':'原单后台核验中，保留预算和素材占用；其他独立调控继续'} for pid in {r['plan_id'] for r in scoped} for r in reservations(db,account['id'],pid)]
            material_scope={}
            if run and data.get('material_source')=='official_api':
                w=self.a.api.workflows.get(account['id']+':'+run['plan_id'],{})
                if w.get('materials_captured_at')==data.get('material_captured_at') and w.get('eligible_range_complete'):
                    floor=w.get('spend_scan_floor')
                    material_scope={'date_start':w.get('date_start'),'date_end':w.get('date_end'),
                        'spend_floor':floor,'material_total':w.get('material_total'),'matched_materials':w.get('matched_materials'),
                        'spend_threshold_materials':w.get('spend_threshold_materials',sum(m['metrics']['spend']>floor for m in w.get('materials',[]) if m['metrics'].get('spend') is not None) if floor is not None else 0)}
            out.append({'account_id':account['id'],'mode':policies.get(account['id'],'api_only'),
                'isolated_readbacks':isolated,
                **material_scope,
                'state':run['state'] if run else 'idle','run_id':run['id'] if run else None,
                'phase':data.get('phase'),'message':data.get('message','尚未开始网页补位'),
                'material_source':data.get('material_source','browser'),'material_captured_at':data.get('material_captured_at'),'yielding':data.get('yielding',False),
                'eligible_range_complete':data.get('eligible_range_complete',False),'unbatched_materials':data.get('unbatched_materials',0),
                'pages_read':data.get('pages_read',0),'materials_read':data.get('materials_read',0),
                'batches_ready':len(data.get('batches',[])),'batches_completed':data.get('completed',0),
                'preview':data.get('preview',False),'single_test':data.get('single_test',False),'updated_at':run['updated_at'] if run else None,'bridge':bridge,
                'readback_only':data.get('readback_only',False),'readback_result':data.get('readback_result'),
                'last_progress_at':data.get('last_progress_at'),'reserved_tasks':list(data.get('reserved_tasks',{}).values()),
                'plan_id':run['plan_id'] if run else None,'phase_label':PHASE_LABELS.get(data.get('phase'),'等待开始'),
                'current_page':data.get('page',1),'tasks_read':len(data.get('tasks',{})),
                'task_total':data.get('task_total'),'last_error':data.get('last_error'),
                'retry_at':data.get('retry_at'),'auto_retry_paused':data.get('auto_retry_paused',False),'retry_available':bool(run and run['state'] not in {'completed','cancelled'} and
                    (not data.get('command') or self.a.clock()>=parse_time(data['command']['expires_at'])))})
        return out

    @staticmethod
    def failure(message,attempt,now,phase):
        raw=str(message)[:300]
        if 'Cannot access contents of the page' in raw or 'manifest must request permission' in raw:
            code='page_access_denied';title='Chrome未允许扩展读取当前投放标签'
            hint='请在原千川页面核对本扩展的网站访问权限，并刷新该页面，然后重试当前阶段。'
        elif '编辑窗口' in raw or '手动选择' in raw:
            code='editor_open';title='页面有未完成的编辑或手动选择，已保留'
            hint='请先处理或关闭页面中的编辑窗口、清除手动选择，再重试。'
        elif '等待可见账户ID' in raw or '等待可见母计划ID' in raw or '自动刷新原投放页一次' in raw:
            code='identity_not_ready';title='原投放页的账户或母计划标识尚未核对成功'
            hint='读取阶段会等待加载并尝试一次刷新；真实编辑窗口会保留。若仍未恢复，请核对该页登录状态后点“重试当前阶段”。'
        elif '素材明细暂显示0条' in raw:
            code='material_detail_pending';title='等待平台返回原任务的素材明细'
            hint='页面目前显示0条素材，尚不能核对原批素材ID。系统只读刷新原单；可重试当前阶段，不能把未加载当作创建成功。'
        elif '详情标签不唯一' in raw:
            code='native_layout';title='当前全域或乘方详情标签未识别'
            hint='旧版全域标签适配已补入扩展1.7.7；加载后重试当前阶段，不需要重发追投。'
        elif '唯一投放页面' in raw:
            code='page_missing';title='未找到本户唯一投放页及对应标签分组'
            hint='保留本户已登录的原Chrome投放页，并放在本户登记的分组内。'
        else:
            code='page_not_ready';title=raw or '网页读取尚未完成'
            hint='可重试当前阶段；若需要退出，请停止本轮补位。'
        delay=300 if code=='page_access_denied' else min(300,30*2**min(attempt-1,4))
        return {'code':code,'title':title,'hint':hint,'raw_message':raw,'attempt':attempt,
            'phase':phase,'phase_label':PHASE_LABELS.get(phase,phase),'at':now.isoformat()},(now+timedelta(seconds=delay)).isoformat()

    def retry(self,p):
        if not isinstance(p,dict) or set(p)!={'account_id'}:raise ValueError('请指定重试账户')
        account=self.a.account(p['account_id'])
        with self.a.local_claim_gate(),self.lock,self.a.db() as db:
            run=db.execute("SELECT * FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled') ORDER BY updated_at DESC LIMIT 1",(account['id'],)).fetchone()
            if not run:raise ValueError('本户没有进行中的网页轮次，请先安排只读验证或开启本户补位')
            data=json.loads(run['data']);command=data.get('command')
            if command and self.a.clock()<parse_time(command['expires_at']):raise ValueError('当前阶段仍在执行，请等本次回传后重试')
            action=db.execute('SELECT state FROM api_actions WHERE id=?',(data.get('action_id'),)).fetchone()
            if action and action['state'] in {'submitting','unknown','submitted_pending_verification'}:data['phase']='verify'
            self.scope(run,data)
            data.pop('retry_at',None);data.pop('auto_retry_paused',None);data['recovery_cycle']=data.get('recovery_cycle',0)+1
            if data.get('last_error'):data['last_error']['attempt']=0
            data['command']=None;data['message']='已安排重试：'+PHASE_LABELS.get(data['phase'],data['phase'])
            self.save(db,run,data)
        self.audit(account['id'],'hybrid_retried',{'message':'用户重试当前网页阶段；已提交单只核验原结果，不重发。','ad_changes':0})
        return {'ok':True,'ad_changes':0}

    def start(self,p):
        """Start one full sweep under the existing F5 monetary authority."""
        if not isinstance(p,dict) or set(p)!={'account_id'}:raise ValueError('请指定按原F5规则启动的账户，不接受额外金额或素材')
        account=self.a.account(p['account_id']);aid=account['id']
        with self.a.local_claim_gate(),self.lock:
            grant=self.a.auto.row(aid)
            if not grant or not grant['enabled']:raise ValueError('本户F5金额及批数授权尚未启用，请先核对原规则与额度')
            if grant.get('execution_mode')=='single_test':raise ValueError('本户仅授权测试一条；尚未开启连续自动追投')
            if self.mode(aid)=='api_only':raise ValueError('请先保存API优先网页补位或仅网页通道')
            with self.a.db() as db:
                old=db.execute("SELECT * FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled') AND COALESCE(json_extract(data,'$.isolated_readback'),0)!=1 ORDER BY updated_at DESC LIMIT 1",(aid,)).fetchone()
            if old and json.loads(old['data']).get('preview'):raise ValueError('本户只读预演仍在进行，请完成或停止预演后再启动实际追投')
            if old:return {'ok':True,'ad_changes':0,'run_id':old['id'],'already_running':True}
            if not self.request(aid,reason='用户按既有F5规则和额度立即启动本轮网页追投'):
                raise ValueError('原Chrome网页执行未开启或连接已过期，请在原扩展开启网页执行后重试')
            with self.a.db() as db:
                run=db.execute("SELECT id FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled') AND COALESCE(json_extract(data,'$.isolated_readback'),0)!=1 ORDER BY updated_at DESC LIMIT 1",(aid,)).fetchone()
        return {'ok':True,'ad_changes':0,'run_id':run['id'],'already_running':False}

    def start_test(self,p):
        """A one-run, one-task grant; never enables the periodic API loop."""
        if not isinstance(p,dict) or set(p)!={'account_id'}:raise ValueError('请指定只测试一条追投的账户')
        aid=self.a.account(p['account_id'])['id']
        with self.a.local_claim_gate(),self.lock:
            with self.a.db() as db:
                old=db.execute("SELECT * FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled')",(aid,)).fetchone()
            if old:
                if json.loads(old['data']).get('single_test'):return {'ok':True,'ad_changes':0,'run_id':old['id'],'already_running':True}
                raise ValueError('本户已有网页轮次，请先停止旧轮次再测试一条')
            if not self.request(aid,reason='用户仅授权测试一条任务，完成后停止，不连续追投',single_test=True):
                raise ValueError('原Chrome网页执行未开启或连接已过期，尚未启动测试')
            with self.a.db() as db:run=db.execute("SELECT id FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled')",(aid,)).fetchone()
        return {'ok':True,'ad_changes':0,'run_id':run['id'],'already_running':False}

    def configure(self,p):
        if not isinstance(p,dict) or set(p)!={'account_id','mode'} or p['mode'] not in MODES:raise ValueError('请选择本户的API或网页执行方式')
        if api_control(self.a) and p['mode'] not in {'api_only','api_data_browser'}:raise ValueError('当前分工仅支持API调控；追投请选择仅API或API取数、网页追投')
        account=self.a.account(p['account_id'])
        if account['scene']!='live':raise ValueError('本轮网页补位先支持直播素材追投')
        old_mode=self.mode(account['id'])
        with self.a.gate,self.lock,self.a.db() as db:
            db.execute('INSERT OR REPLACE INTO hybrid_policy VALUES(?,?,?)',(account['id'],p['mode'],self.now()))
        if p['mode']=='api_only' or old_mode!=p['mode'] and 'api_data_browser' in {old_mode,p['mode']}:self.stop({'account_id':account['id']})
        self.audit(account['id'],'hybrid_mode_changed',{'message':'追投通道已设为'+{'api_only':'仅API','api_preferred':'API优先，允许网页补位','browser_only':'仅网页','api_data_browser':'API取数、网页追投'}[p['mode']],'ad_changes':0})
        return {'ok':True,'ad_changes':0}

    def stop(self,p):
        if not isinstance(p,dict) or set(p)!={'account_id'}:raise ValueError('请指定停止补位的账户')
        self.a.account(p['account_id'])
        with self.a.gate,self.lock,self.a.db() as db:
            for run in db.execute("SELECT * FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled')",(p['account_id'],)).fetchall():
                data=json.loads(run['data']);aid=data.get('action_id')
                data['stop_requested']=True
                row=db.execute('SELECT state FROM api_actions WHERE id=?',(aid,)).fetchone() if aid else None
                if row and row['state'] in {'submitting','unknown','submitted_pending_verification'}:
                    data['phase']='verify';data['message']='已停止新建，仅回读原提交结果';self.save(db,run,data)
                else:
                    if row and row['state']=='draft':db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=?",(self.now(),self.dump({'message':'网页补位已停止，尚未提交。','ad_changes':0}),aid))
                    data['message']='网页读取/准备已停止，未提交';self.save(db,run,data,'cancelled')
        return {'ok':True,'ad_changes':0}

    def reset_browser_worker(self,aid,worker_id):
        """Retire only this worker's leases; a sent action is never replayed."""
        with self.a.gate,self.lock,self.a.db() as db:
            for run in db.execute("SELECT * FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled')",(aid,)).fetchall():
                data=json.loads(run['data']);command=data.get('command') or {}
                if command.get('worker_id')!=worker_id and not str(command.get('worker_id','')).startswith('ego:'):continue
                row=db.execute('SELECT state FROM api_actions WHERE id=?',(data.get('action_id'),)).fetchone()
                if row and row['state'] in {'submitting','unknown','submitted_pending_verification'} or ((data.get('permit') or {}).get('consumed') is True and (data.get('permit') or {}).get('action_id')==data.get('action_id')):
                    data.update(command=None,phase='verify',message='本户执行器已重启；原提交保留占用，仅通过API核验，不重复创建')
                    self.save(db,run,data,'waiting');continue
                if row and row['state'] in {'draft','checking'}:
                    db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state IN ('draft','checking')",(self.now(),self.dump({'message':'重启本户执行器，取消尚未提交的旧准备单','ad_changes':0}),data['action_id']))
                data.update(command=None,message='重启本户执行器，旧未提交轮次已结束；下轮使用当前规则')
                self.save(db,run,data,'cancelled')
                db.execute('UPDATE api_auto SET next_at=? WHERE account_id=? AND grant_id=? AND enabled=1',(self.now(),aid,data.get('grant_id')))

    def recheck(self,p):
        """Read a previously verified browser task without reopening its write."""
        if not isinstance(p,dict) or set(p)!={'action_id'}:raise ValueError('请指定原网页调控单ID')
        with self.a.gate,self.lock,self.a.db() as db:
            row=db.execute('SELECT * FROM api_actions WHERE id=?',(p['action_id'],)).fetchone()
            if not row or row['state']!='verified':raise ValueError('只读复核仅支持已核验的原网页追投单；待核验单请重试原阶段')
            intent=json.loads(row['intent']);result=json.loads(row['result'] or '{}')
            if intent.get('execution_channel')!='browser' or intent.get('action')!='material_chase' or not result.get('task_id'):raise ValueError('原单不是已有实际任务ID的网页追投')
            account=self.a.account(intent['account_id']);exact_id(result['task_id'])
            old=db.execute("SELECT * FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled')",(account['id'],)).fetchone()
            if old:
                data=json.loads(old['data'])
                if data.get('readback_only') and data.get('action_id')==row['id']:return {'ok':True,'run_id':old['id'],'ad_changes':0}
                raise ValueError('本户已有网页轮次，完成后再只读复核原单')
            rid=secrets.token_hex(16)
            data={'phase':'verify','page':1,'action_id':row['id'],'preview':True,'readback_only':True,
                'reason':'只读复核原已创建任务','tasks':{},'lookups':{},'batches':[],'seen_materials':[],
                'pages_read':0,'materials_read':0,'completed':0,'begin_at':self.now(),
                'message':'等待程序只读复核原任务 '+result['task_id']+'，不创建新追投。','command':None,'started_at':self.now()}
            db.execute('INSERT INTO hybrid_runs VALUES(?,?,?,?,?,?)',(rid,account['id'],intent['plan_id'],'waiting',self.now(),self.dump(data)))
        self.audit(account['id'],'hybrid_readback_requested',{'action_id':row['id'],'task_id':result['task_id'],'message':data['message'],'ad_changes':0})
        return {'ok':True,'run_id':rid,'ad_changes':0}

    def exclusions_changed(self,aid):
        with self.a.gate,self.lock,self.a.db() as db:
            for run in db.execute("SELECT * FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled')",(aid,)).fetchall():
                data=json.loads(run['data']);action=data.get('action_id')
                row=db.execute('SELECT state FROM api_actions WHERE id=?',(action,)).fetchone() if action else None
                if row and row['state'] in {'submitting','unknown','submitted_pending_verification'}:
                    data['exclusions_dirty']=True
                else:
                    if row:db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state IN ('draft','checking')",(self.now(),self.dump({'message':'排除素材列表变化，原未提交批次已取消；重新筛选。','ad_changes':0}),action))
                    data.pop('action_id',None)
                    self.reset_materials(data,'排除素材列表已更新，按新列表重新筛选；未提交。')
                    data['preflight']=False
                self.save(db,run,data)

    def audit(self,aid,event,detail):self.a.auto.audit(aid,event,detail)

    def active(self,aid,*,writes_only=False):
        with self.a.db() as db:
            return db.execute("SELECT 1 FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled') AND COALESCE(json_extract(data,'$.isolated_readback'),0)!=1 AND (?=0 OR (COALESCE(json_extract(data,'$.yielding'),0)!=1 AND COALESCE(json_extract(data,'$.readback_only'),0)!=1 AND (COALESCE(json_extract(data,'$.preview'),0)!=1 OR json_extract(data,'$.action_id') IS NOT NULL))) LIMIT 1",(aid,int(writes_only))).fetchone() is not None

    def read_only_active(self,aid):
        with self.a.db() as db:rows=db.execute("SELECT data FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled') AND COALESCE(json_extract(data,'$.isolated_readback'),0)!=1",(aid,)).fetchall()
        return bool(rows) and all(not (d:=json.loads(r['data'])).get('action_id') and not d.get('single_test') and d['phase'] in {'tasks','lookup','materials','api_materials'} for r in rows)

    def inventory_read_in_progress(self,d):
        if d.get('material_source')!='official_api' or d.get('phase')!='tasks' or d.get('last_error') or d.get('auto_retry_paused'):return False
        command=d.get('command') or {}
        if command and self.a.clock()<parse_time(command['expires_at']) or d.get('tasks') and len(d['tasks'])<d.get('task_total',0):return True
        # The API sweep may finish between two worker leases. That queued,
        # empty first inventory is still real work: yielding in this gap kept
        # restarting F5 before it could ever prepare one batch. Protect only
        # fresh candidates whose native inventory is absent or older, then
        # use the existing one-batch handoff to let rules resume.
        if not (d.get('api_pools') or d.get('batches')):return False
        try:
            captured=parse_time(d.get('material_captured_at'))
            fresh=0<=(self.a.clock()-captured).total_seconds()<=600
            return fresh and (not d.get('direct_inventory_complete') or not d.get('inventory_at') or parse_time(d['inventory_at'])<captured)
        except (ValueError,TypeError):return False

    def yield_readonly(self,aid):
        with self.a.gate,self.lock,self.a.db() as db:
            if not self.read_only_active(aid):return False
            runs=db.execute("SELECT * FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled') AND COALESCE(json_extract(data,'$.isolated_readback'),0)!=1",(aid,)).fetchall()
            # A scheduler turn is indivisible. Revoking a live read lease here
            # aborts its native RPC and discards otherwise valid progress.
            if any((cmd:=json.loads(r['data']).get('command')) and self.a.clock()<parse_time(cmd['expires_at']) for r in runs):return False
            for run in runs:
                d=json.loads(run['data'])
                if d.get('preview') and d.get('material_source')=='official_api':continue
                if d.get('material_source')=='official_api':
                    d.update(command=None,yielding=True,message='主巡航优先，已保留API素材候选；原调控结束后继续本轮。')
                    self.save(db,run,d);continue
                d.update(command=None,message='本轮仅在读取，先让已命中规则的主巡航执行；F5按原周期继续。')
                self.save(db,run,d,'cancelled')
        return True

    def preview_ready(self,db,account_id=None):
        rows=db.execute("SELECT * FROM hybrid_runs WHERE state NOT IN ('completed','cancelled') AND (? IS NULL OR account_id=?) AND json_extract(data,'$.preview')=1 AND COALESCE(json_extract(data,'$.readback_only'),0)!=1 AND json_extract(data,'$.phase') IN ('tasks','dry_prepare','dom_probe') AND COALESCE(json_extract(data,'$.auto_retry_paused'),0)!=1 AND (json_extract(data,'$.retry_at') IS NULL OR json_extract(data,'$.retry_at')<=?)",(account_id,account_id,self.now())).fetchall()
        return any(not self.pending(db,r['account_id'],r['plan_id'],json.loads(r['data']).get('action_id')) for r in rows)

    def request_probe(self,p):
        if not isinstance(p,dict) or set(p)!={'account_id','target','selectors'}:raise ValueError('只读诊断参数无效')
        if p['target'] not in {'home','detail'} or not isinstance(p['selectors'],list) or not 1<=len(p['selectors'])<=6:raise ValueError('只读诊断范围无效')
        if any(not isinstance(s,str) or not 1<=len(s)<=180 for s in p['selectors']):raise ValueError('只读选择器无效')
        if self.mode(p['account_id'])!='api_data_browser':raise ValueError('本次诊断限已登记的API取数网页追投账户')
        self.request(p['account_id'],reason='用户请求只读页面结构诊断',preview=True,probe={'target':p['target'],'selectors':p['selectors']})
        return {'ok':True,'ad_changes':0}

    def request(self,aid,*,reason,preview=False,single_test=False,probe=None):
        if probe is not None and (not preview or single_test):raise ValueError('结构诊断只能只读执行')
        api_read_preview=preview and (self.mode(aid)=='api_data_browser' or api_control(self.a))
        if hasattr(self.a,'hybrid_rules') and self.a.hybrid_rules.active(aid) and not api_read_preview:return False
        account=self.a.account(aid)
        if account['scene']!='live':raise ValueError('网页素材追投仅支持直播户')
        grant=self.a.auto.row(aid);plan_source='api_auto'
        if preview and not grant:
            grant=self.a.rule_auto.row(aid);plan_source='rule_auto'
        if not grant or not grant['plan_id']:raise ValueError('请先为本户选择自动追投母计划')
        if not preview and (self.mode(aid)=='api_only' or not single_test and (not grant['enabled'] or grant.get('execution_mode')=='single_test')):return False
        if not preview and self.mode(aid)!='api_data_browser' and not api_control(self.a):
            with self.a.db() as db:client=db.execute('SELECT armed,seen_at FROM hybrid_clients ORDER BY seen_at DESC LIMIT 1').fetchone()
            if not client or not client['armed'] or not 0<=(self.a.clock()-parse_time(client['seen_at'])).total_seconds()<=120:return False
        profile,revision=self.a.profile(aid)
        if single_test:
            if revision!=grant['profile_revision'] or digest(profile)!=grant['profile_digest']:raise ValueError('已保存的F5规则已变化，请先核对本户规则和额度')
            if not self.a.auto.limits_complete(grant):raise ValueError('请先填写本户在投预算上限和每日批数，或明确选择无上限，不会为测试补填额度')
        if not preview:self.a.conflict_check()
        with self.a.gate,self.lock,self.a.db() as db:
            old=db.execute("SELECT * FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled') AND COALESCE(json_extract(data,'$.isolated_readback'),0)!=1",(aid,)).fetchone()
            if old:
                prior=json.loads(old['data'])
                if preview and self.mode(aid)=='api_data_browser' and not prior.get('preview'):
                    action=db.execute('SELECT state FROM api_actions WHERE id=?',(prior.get('action_id'),)).fetchone()
                    if action and action['state'] in {'submitting','unknown','submitted_pending_verification'}:raise ValueError('原提交正在核验，不能替换为新表单预演')
                    if action:db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=? AND state IN ('draft','checking')",(self.now(),prior['action_id']))
                    prior.update(command=None,message='按用户请求切换为只读预演，原未提交轮次已取消。')
                    self.save(db,old,prior,'cancelled')
                elif probe is not None:raise ValueError('本户原只读阶段仍在运行，先等待其完成')
                else:return True
            pending=self.pending(db,aid,grant['plan_id'])
            if pending and not api_read_preview:raise ValueError('原计划有待处理或结果不明动作，网页不会重复新建')
            rid=secrets.token_hex(16)
            if single_test:
                grant={**grant,'grant_id':secrets.token_hex(16)}
                db.execute("UPDATE api_auto SET grant_id=?,enabled=1,execution_mode='single_test',test_run_id=?,state='single_test',message='仅测试一条网页追投任务，完成后停止；连续自动追投关闭。' WHERE account_id=?",(grant['grant_id'],rid,aid))
            data={'phase':'tasks','page':1,'grant_id':grant['grant_id'],'revision':revision,
                'single_test':single_test,
                'profile_digest':digest(profile),'preview':preview,'preview_plan_source':plan_source,'reason':reason,'tasks':{},'lookups':{},
                'batches':[],'seen_materials':[],'pages_read':0,'materials_read':0,'completed':0,
                'message':'等待原Chrome读取在投任务和素材；'+reason,'command':None,'day':self.a.clock().date().isoformat(),'started_at':self.now()}
            if probe is not None:
                data.update(phase='dom_probe',probe=copy.deepcopy(probe),message='等待原Chrome只读回传页面结构，不填写或提交广告。')
            elif self.mode(aid)=='api_data_browser' or api_control(self.a):
                data.update(phase='api_materials',material_source='official_api',message='等待API按消耗降序读取近几天素材；网页仅核对本批ID、预算并追投。')
            db.execute('INSERT INTO hybrid_runs VALUES(?,?,?,?,?,?)',(rid,aid,grant['plan_id'],'waiting',self.now(),self.dump(data)))
        self.audit(aid,'hybrid_requested',{'message':data['message'],'preview':preview,'ad_changes':0})
        return True

    @staticmethod
    def dump(v):return json.dumps(v,ensure_ascii=False,separators=(',',':'),allow_nan=False)

    @staticmethod
    def pending(db,aid,pid,exclude=None):
        from .chase_recovery import reservations,held_native_budget
        isolated={r['action_id'] for r in reservations(db,aid,pid)}
        held={(r['action_id'],r['task_id']) for r in db.execute('SELECT action_id,task_id FROM api_task_holds WHERE account_id=? AND plan_id=? AND active=1',(aid,pid))}
        rows=[]
        for r in db.execute('SELECT * FROM api_actions WHERE state IN ('+','.join('?' for _ in PENDING)+') AND json_extract(intent,\'$.account_id\')=? AND json_extract(intent,\'$.plan_id\')=?',(*PENDING,aid,pid)):
            if r['id']==exclude or r['id'] in isolated:continue
            i=json.loads(r['intent'])
            # A precisely held, acknowledged-uncertain task budget remains
            # reserved by budget_tasks_with_holds. It must not prevent the F5
            # reader/start/final gates from working on other materials. Keep
            # actively submitting, unheld, malformed and unknown creations
            # blocked; holding is never a success receipt or released money.
            if r['state'] in {'unknown','submitted_pending_verification'} and (r['id'],i.get('task_id')) in held and held_native_budget(i) is not None:continue
            rows.append(r)
        rows.extend(db.execute("SELECT * FROM api_followups WHERE account_id=? AND plan_id=? AND state IN ('submitting','unknown')",(aid,pid)).fetchall())
        rows.extend(db.execute("SELECT * FROM api_schedules WHERE account_id=? AND plan_id=? AND state IN ('creating','stopping','unknown')",(aid,pid)).fetchall())
        return rows

    @classmethod
    def pending_blocks(cls,db,aid,pid,exclude,phase):
        # A run that only reads back its own original must stay dispatchable
        # while every other pending row of the plan is a task the operator has
        # already skipped. A skip resolves that task, so it cannot collide with
        # a read, while still keeping its unknown budget reservation. Without
        # this the verify phase is parked by an unrelated held action and can
        # never establish the result of its own original.
        rows=cls.pending(db,aid,pid,exclude)
        if not rows:return False
        if phase=='verify':
            held={r['action_id'] for r in db.execute('SELECT action_id FROM api_task_holds WHERE account_id=? AND plan_id=? AND active=1',(aid,pid))}
            if all(r['id'] in held for r in rows):return False
        return True

    def save(self,db,run,data,state='waiting'):
        signature=digest({'phase':data.get('phase'),'page':data.get('page'),'lookup':data.get('lookup_id'),
            'tasks':sorted(data.get('tasks',{})),'lookups':sorted(data.get('lookups',{})),'reserved':sorted(data.get('reserved_tasks',{})),
            'pages':data.get('pages_read',0),'materials':data.get('materials_read',0),'completed':data.get('completed',0),
            'batches':[b['material_ids'] for b in data.get('batches',[])]})
        seen=data.setdefault('progress_signatures',[])
        if signature not in seen:
            data['last_progress_at']=self.now();seen.append(signature);data['progress_signatures']=seen[-128:]
        db.execute('UPDATE hybrid_runs SET state=?,updated_at=?,data=? WHERE id=?',(state,self.now(),self.dump(data),run['id']))
        if data.get('single_test') and state in {'completed','cancelled'}:
            db.execute("UPDATE api_auto SET enabled=0,state=?,message=? WHERE account_id=? AND execution_mode='single_test' AND test_run_id=?",('test_completed' if state=='completed' else 'test_stopped',data.get('message','单条测试已结束'),run['account_id'],run['id']))

    def scope(self,run,data):
        account=self.a.account(run['account_id'])
        if data['phase']=='verify':return account,self.a.profile(account['id'])[0],None
        if data.get('day')!=self.a.clock().date().isoformat():raise ValueError('统计日期已跨天，请结束旧轮次后重新扫描')
        profile,revision=self.a.profile(account['id'])
        grant=self.a.rule_auto.row(account['id']) if data.get('preview') and data.get('preview_plan_source')=='rule_auto' else self.a.auto.row(account['id'])
        if not grant or grant['grant_id']!=data['grant_id'] or grant['plan_id']!=run['plan_id']:raise ValueError('本户自动追投授权已变化，请重新扫描')
        if revision!=data['revision'] or digest(profile)!=data['profile_digest']:raise ValueError('本户规则已变化，请重新扫描')
        if not data['preview']:
            if data.get('stop_requested'):raise ValueError('本轮已停止，不再创建新追投')
            self.a.auto.verify_scope({'account_id':account['id'],'plan_id':run['plan_id'],'auto_authority':{'grant_id':grant['grant_id']},'test_run_id':run['id'] if data.get('single_test') else None},exclude=data.get('action_id'))
            if self.mode(account['id'])=='api_only':raise ValueError('本户已切回仅API，未提交网页追投')
        return account,profile,grant

    def worker(self,p):
        required={'protocol','worker_id','epoch','armed','revision'}
        if not isinstance(p,dict) or not required<=set(p) or set(p)-required-{'extension_version'} or p['protocol']!=1 or type(p['armed']) is not bool:raise ValueError('网页补位协议无效')
        executor=getattr(self.a,'browser_executor',None)
        if executor:executor.guard_worker(p)
        policy=self.policy()
        if p['revision']!=policy['revision']:raise ValueError('采集范围已变化')
        for key in ('worker_id','epoch'):
            if not isinstance(p[key],str) or not 1<=len(p[key])<=100:raise ValueError('执行会话无效')
        version=p.get('extension_version','unknown')
        if not isinstance(version,str) or len(version)>30:raise ValueError('扩展版本无效')
        with self.a.db() as db:db.execute('INSERT OR REPLACE INTO hybrid_clients VALUES(?,?,?,?)',(p['worker_id'],version,int(p['armed']),self.now()))
        if current_writer(version):
            scope_id=getattr(executor.session(),'account_id',None) if executor and executor.selected() else None
            with self.a.gate,self.lock,self.a.db() as db:
                for run in db.execute("SELECT * FROM hybrid_runs WHERE state NOT IN ('completed','cancelled') AND (? IS NULL OR account_id=?) AND json_extract(data,'$.auto_retry_paused')=1",(scope_id,scope_id)).fetchall():
                    data=json.loads(run['data'])
                    if (data.get('last_error') or {}).get('extension_version')!=version:
                        data.pop('auto_retry_paused',None);data.pop('retry_at',None);data['recovery_cycle']=data.get('recovery_cycle',0)+1
                        data['message']='新版扩展已接通，重新核对原读取阶段；已提交任务只回读。';self.save(db,run,data)
                if precise_selection_reader(version):
                    for run in db.execute("SELECT * FROM hybrid_runs WHERE state NOT IN ('completed','cancelled') AND (? IS NULL OR account_id=?)",(scope_id,scope_id)).fetchall():
                        data=json.loads(run['data'])
                        if data.get('material_source')!='official_api' or data.get('selection_reader_fixed') or data['phase']=='verify':continue
                        action=db.execute('SELECT state FROM api_actions WHERE id=?',(data.get('action_id'),)).fetchone()
                        if action and action['state'] in {'submitting','unknown','submitted_pending_verification'}:continue
                        data['selection_reader_fixed']=True
                        if data.get('unavailable_materials'):
                            if action:db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=? AND state IN ('draft','checking')",(self.now(),data['action_id']))
                            data.pop('action_id',None);data['unavailable_materials']=[]
                            self.reset_materials(data,'新版已修正选择框读取，旧版未明确的不可选结果已解除；重新核对候选，永久排除名单不变。')
                            data['preflight']=False;self.save(db,run,data)
                        else:self.save(db,run,data,run['state'])
        return policy

    def ping(self,p):
        self.worker(p)
        return {'ok':True,'extension_version':p.get('extension_version','unknown'),'armed':p['armed'],'ad_changes':0}

    def next(self,p,*,account_id=None):
        policy=self.worker(p)
        with self.a.gate,self.lock,self.a.db() as db:
            candidates=db.execute("SELECT * FROM hybrid_runs WHERE state NOT IN ('completed','cancelled') AND (? IS NULL OR account_id=?) AND json_extract(data,'$.phase')!='api_materials' AND COALESCE(json_extract(data,'$.yielding'),0)!=1 AND COALESCE(json_extract(data,'$.auto_retry_paused'),0)!=1 AND (json_extract(data,'$.retry_at') IS NULL OR json_extract(data,'$.retry_at')<=?) ORDER BY CASE WHEN json_extract(data,'$.phase')='verify' THEN 0 WHEN json_extract(data,'$.preview')=1 THEN 1 ELSE 2 END,updated_at,id",(account_id,account_id,self.now())).fetchall()
            # One account's original unknown action must not consume every
            # browser poll while other accounts have a runnable read/preview.
            run=None
            for candidate in candidates:
                candidate_data=json.loads(candidate['data'])
                if api_control(self.a):
                    if self.api_lane.migrate(db,candidate,candidate_data):continue
                    if candidate_data['phase'] not in {'prepare','dry_prepare'}:continue
                if self.pending_blocks(db,candidate['account_id'],candidate['plan_id'],candidate_data.get('action_id'),candidate_data.get('phase')):
                    candidate_data['message']='本母计划原动作待核验，本户暂缓网页操作；其他账户可继续。'
                    self.save(db,candidate,candidate_data)
                    continue
                run=candidate;break
            if not run:return {'ok':True,'command':None}
            data=json.loads(run['data'])
            try:
                if data.get('action_id') and data['phase']!='verify':
                    original=db.execute('SELECT state FROM api_actions WHERE id=?',(data['action_id'],)).fetchone()
                    if original and original['state'] in {'submitting','unknown','submitted_pending_verification'}:
                        data.update(phase='verify',command=None,message='原提交结果待核验，只回读原单，不重发。')
                account,profile,grant=self.scope(run,data)
                if data.get('material_source')=='official_api' and data['phase'] in {'prepare','dry_prepare'} and data.get('action_id'):
                    candidate=db.execute('SELECT state,intent FROM api_actions WHERE id=?',(data['action_id'],)).fetchone()
                    if candidate and candidate['state'] in {'draft','checking'}:
                        captured=json.loads(candidate['intent'])['browser_batch']['scanned_at']
                        if not 0<=(self.a.clock()-parse_time(captured)).total_seconds()<=600:
                            self.retire_api_draft(db,data)
                            self.reset_materials(data,'旧准备单的API素材已过期，重新取数后生成新批次；未打开旧单表单。')
                            self.save(db,run,data,'waiting');return {'ok':True,'command':None}
                if api_control(self.a):
                    try:current=direct_home_writer(p) and tuple(map(int,p['extension_version'].split('.')))>=(1,23,0)
                    except (KeyError,ValueError):current=False
                    if not current:return {'ok':True,'command':None,'message':'等待Ego 1.23.0，只允许新建追投'}
                if data['phase']=='dom_probe' and not dom_probe_reader(p.get('extension_version')):
                    data['message']='等待扩展1.11.3的只读结构回传；未提交广告。';self.save(db,run,data)
                    return {'ok':True,'command':None,'message':data['message']}
                if data.get('material_source')=='official_api' and data['phase'] not in {'verify','api_materials'} and not api_material_writer(p.get('extension_version')):
                    data['message']='API素材候选已就绪，等待原Chrome加载扩展1.10.0；网页不会扫描素材列表或提交。';self.save(db,run,data)
                    return {'ok':True,'command':None,'message':data['message']}
                if data['phase']=='api_materials':return {'ok':True,'command':None,'message':data['message']}
                if data.get('material_source')=='official_api' and data['phase']!='verify' and not 0<=(self.a.clock()-parse_time(data['material_captured_at'])).total_seconds()<=600:
                    if data.get('action_id'):
                        db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=? AND state IN ('draft','checking')",(self.now(),data['action_id']))
                        data.pop('action_id',None)
                    self.reset_materials(data,'API素材数据超过10分钟，先重新取数再核对网页；未提交。');data['preflight']=False
                    self.save(db,run,data);return {'ok':True,'command':None,'message':data['message']}
                if data.get('material_source')=='official_api' and data['phase'] in {'tasks','prepare','dry_prepare'} and data.get('batches') and not profile['features']['f5'].get('allow_running_materials',False):
                    snapshot=self.api_active_snapshot(run,data)
                    if snapshot:
                        data['api_active_snapshot']=snapshot
                        pool={m['id'] for p in data.get('api_pools',[]) for m in p['materials']}
                        active=set(snapshot['material_ids'])&pool
                        overlap=active&set(data['batches'][0]['material_ids'])
                        if overlap and self.skip_running_candidate(db,run,data,sorted(overlap)[0],profile,source='api',active_ids=active):
                            if not data.get('batches') or data.get('finished') or data['phase']=='api_materials':return {'ok':True,'command':None,'message':data['message']}
                if data['phase']=='prepare' and not current_writer(p.get('extension_version')):
                    data['message']='等待原Chrome加载扩展1.8.0；本批未获得提交许可。';self.save(db,run,data)
                    return {'ok':True,'command':None,'message':data['message']}
                if data.get('material_source')=='official_api' and direct_home_writer(p) and data['phase'] in {'prepare','dry_prepare'} and (not data.get('direct_inventory_complete') or not data.get('inventory_at') or (self.a.clock()-parse_time(data['inventory_at'])).total_seconds()>180):
                    if data.get('action_id'):db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=? AND state='draft'",(self.now(),data.pop('action_id')))
                    data.update(phase='api_inventory' if api_control(self.a) else 'tasks',page=1,tasks={},lookups={},command=None,direct_inventory_complete=False);data.pop('task_total',None)
                    if api_control(self.a):self.save(db,run,data);return {'ok':True,'command':None}
                action_id=data.get('action_id')
                if self.pending_blocks(db,run['account_id'],run['plan_id'],action_id,data['phase']):raise ValueError('原计划还有待处理动作，网页补位等待核验')
                if data['phase']!='verify' and not data['preview'] and not p['armed']:
                    if data.get('material_source')=='official_api':
                        data['message']='API素材已读取，等待'+self.executor_name()+'开启网页执行；尚未提交。';self.save(db,run,data)
                        return {'ok':True,'command':None,'message':data['message']}
                    raise ValueError('原Chrome执行模式关闭；网页补位等待开启')
                command=data.get('command')
                if data['phase']=='verify' and command and (command['worker_id'],command['epoch'])!=(p['worker_id'],p['epoch']):
                    # A reload may immediately replace a read lease. The old
                    # receipt then fails its lease check; no create is replayed.
                    command=None;data['command']=None
                if command and self.a.clock()<parse_time(command['expires_at']):
                    if (command['worker_id'],command['epoch'])!=(p['worker_id'],p['epoch']):return {'ok':True,'command':None}
                else:
                    if action_id:
                        action=db.execute('SELECT * FROM api_actions WHERE id=?',(action_id,)).fetchone()
                        if action and action['state'] in {'submitting','unknown','submitted_pending_verification'}:data['phase']='verify'
                        elif action and action['state'] not in {'draft','checking'} and not data.get('readback_only'):
                            data['message']='原网页单已处理，等待下一轮';self.save(db,run,data,'completed');return {'ok':True,'command':None}
                    prepare_seconds=180 if direct_home_writer(p) and data['phase'] in {'prepare','dry_prepare'} and api_control(self.a) else 120
                    command={'id':secrets.token_hex(16),'lease':secrets.token_hex(24),'worker_id':p['worker_id'],'epoch':p['epoch'],
                        'extension_version':p.get('extension_version','unknown'),
                        'expires_at':(self.a.clock()+timedelta(seconds=prepare_seconds)).isoformat()}
                    data['command']=command
                if data['phase'] in {'prepare','dry_prepare'} and data.get('material_source')=='official_api' and parent_anchor_writer(command.get('extension_version')):
                    anchor=self.parent_anchor(run)
                    if anchor:command['parent_anchor']=anchor
                    else:command.pop('parent_anchor',None)
                command={**command,'run_id':run['id'],'phase':data['phase'],'account_id':account['id'],'plan_id':run['plan_id'],
                    'group':account['tab_group'],'page':data.get('page',1),'preview':data['preview'],
                    'date_start':date_range(self.a.clock(),profile['features']['f5'].get('lookback_days',5))[0],
                    'date_end':self.a.clock().date().isoformat(),'lookup_id':data.get('lookup_id'),
                    'document_cycle':data.get('verify_cycle',0),
                    'recovery_cycle':data.get('recovery_cycle',0),
                    'action':self.a.record(db.execute('SELECT * FROM api_actions WHERE id=?',(action_id,)).fetchone()) if action_id else None}
                if api_control(self.a):command['execution_scope']='f5_create_only'
                if data.get('material_source')=='official_api' and data.get('eligible_range_complete') and data.get('material_captured_at'):
                    command['account_binding']={'kind':'official_api_material_scope','account_id':account['id'],'plan_id':run['plan_id'],
                        'verified_at':data['material_captured_at']}
                if data.get('material_source')=='official_api' and direct_home_writer(p):
                    command['direct_home']=True
                    if data['phase']=='tasks':
                        command['inventory_page_size']=10
                        # The page's “共0条视频”/零读取只能由API任务清单补齐；候选池仍需5分钟内新。
                        inventory=self.api_active_snapshot(run,data,max_age=1800)
                        if inventory and inventory.get('task_materials'):command['inventory_api_materials']=inventory['task_materials']
                if data['phase']=='prepare':command['page']=command['action']['intent']['browser_batch']['page']
                if data.get('readback_only') or data['phase']=='verify':
                    command['date_end']=command['date_start']=parse_time(command['action']['created_at']).date().isoformat()
                if data['phase']=='lookup':
                    original=db.execute("SELECT created_at,intent FROM api_actions WHERE json_extract(intent,'$.account_id')=? AND json_extract(intent,'$.plan_id')=? AND CAST(json_extract(result,'$.task_id') AS TEXT)=? ORDER BY created_at LIMIT 1",(run['account_id'],run['plan_id'],data['lookup_id'])).fetchone()
                    if original:
                        # Search the task on its original creation day. The page
                        # date may otherwise hide a task that ended before today.
                        command['date_end']=command['date_start']=parse_time(original['created_at']).date().isoformat()
                        if data.get('lookup_attempts',{}).get(data['lookup_id'],0)>=1:
                            native=json.loads(original['intent'])
                            name=native.get('browser_batch',{}).get('task_name') or native.get('body',{}).get('name')
                            if isinstance(name,str) and name.strip():command['lookup_id']=name
                if data['phase']=='dry_prepare':
                    batch=copy.deepcopy(data['batches'][0]);rule=profile['features']['f5']['rules'][batch['rule_index']]
                    fields=('chase_type','total_budget','control_duration') if rule['chase_type']=='放量追投' else ('chase_type','daily_budget','bid_value','optimization_target')
                    batch.update(after={k:rule[k] for k in fields},task_name='QCX_PREVIEW_'+run['id'][:16],allow_running_materials=profile['features']['f5'].get('allow_running_materials',False))
                    from .chase_audience import configured,effective
                    if configured(rule):batch['after']['audience']=effective(rule)
                    command['action']={'id':'preview_'+run['id'],'intent':{'browser_batch':batch}}
                    command['page']=batch['page']
                if data['phase'] in {'prepare','dry_prepare'} and data.get('material_source')=='official_api' and home_entry_writer(command.get('extension_version')):
                    command['preferred_entry']='home_plus'
                if data['phase']=='dom_probe':command['probe']=copy.deepcopy(data['probe'])
                data['message']='网页补位：'+PHASE_LABELS.get(data['phase'],data['phase'])
                self.save(db,run,data,'working')
                return {'ok':True,'command':command}
            except ValueError as exc:
                data['message']=str(exc)
                obsolete=any(word in str(exc) for word in ('授权已变化','规则已变化','统计日期已跨天','切回仅API','授权已关闭','执行模式关闭'))
                if obsolete and data['phase']!='verify':
                    if data.get('action_id'):db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=? AND state='draft'",(self.now(),data['action_id']))
                    self.save(db,run,data,'cancelled')
                else:self.save(db,run,data)
                return {'ok':True,'command':None,'message':str(exc)}

    def tick(self):
        # A forgotten read-only diagnostic must not occupy a live account forever.
        with self.a.gate,self.lock,self.a.db() as db:
            for run in db.execute("SELECT * FROM hybrid_runs WHERE state NOT IN ('completed','cancelled')").fetchall():
                data=json.loads(run['data'])
                if api_control(self.a) and self.api_lane.migrate(db,run,data):continue
                # A read failure must expire independently of priority yielding.
                # Otherwise every rule scan renews the apparent resume time and
                # a five-failure F5 read can remain paused for hours.
                held_action=db.execute('SELECT state FROM api_actions WHERE id=?',(data.get('action_id'),)).fetchone()
                uncertain_read=held_action and held_action['state'] in {'submitting','unknown','submitted_pending_verification'}
                cmd=data.get('command');leased_read=cmd and self.a.clock()<parse_time(cmd['expires_at'])
                if (data.get('auto_retry_paused') or data.get('read_stalled_at')) and not uncertain_read and not leased_read and data['phase']!='verify' and (self.a.clock()-parse_time(data.get('read_stalled_at') or (data.get('last_error') or {}).get('at') or data.get('last_progress_at') or data.get('started_at') or run['updated_at'])).total_seconds()>=600:
                    if held_action and held_action['state'] in {'draft','checking'}:db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=?",(self.now(),self.dump({'message':'读取连续失败后已自动结束未提交轮次，下周期重新读取；没有提交投放。','ad_changes':0}),data['action_id']))
                    data.update(command=None,message='读取连续失败后已自动结束旧轮次，下周期重新读取；不再被规则优先等待无限延长。')
                    self.save(db,run,data,'cancelled')
                    db.execute("UPDATE api_auto SET next_at=?,state='waiting',message=? WHERE account_id=? AND grant_id=? AND enabled=1",(self.now(),data['message'],run['account_id'],data.get('grant_id')))
                    self.audit(run['account_id'],'hybrid_read_stall_released',{'message':data['message'],'ad_changes':0})
                    continue
                if data.get('yielding'):
                    if hasattr(self.a,'hybrid_rules') and self.a.hybrid_rules.active(run['account_id']):continue
                    if self.pending(db,run['account_id'],run['plan_id'],data.get('action_id')):continue
                    if data.get('rule_yield_queued_until') and self.a.clock()<parse_time(data['rule_yield_queued_until']):continue
                    data.pop('rule_yield_queued_until',None)
                    data.pop('yielding',None);data.update(command=None,resumed_at=self.now(),message='主巡航原调控已结束，继续保留的API素材候选；重新核对在投预算。')
                    if data['phase'] in {'tasks','lookup'}:
                        data.update(phase='tasks',page=1,tasks={},lookups={});data.pop('task_total',None)
                    self.save(db,run,data)
                action=db.execute('SELECT state FROM api_actions WHERE id=?',(data.get('action_id'),)).fetchone()
                uncertain=action and action['state'] in {'submitting','unknown','submitted_pending_verification'}
                command=data.get('command') or {}
                leased=command and self.a.clock()<parse_time(command['expires_at'])
                if uncertain and data['phase']=='verify' and not leased and not data.get('isolated_readback') and data.get('permit',{}).get('consumed') is True and (self.a.clock()-parse_time(data.get('begin_at',run['updated_at']))).total_seconds()>=600:
                    original=db.execute('SELECT * FROM api_actions WHERE id=?',(data['action_id'],)).fetchone();intent=json.loads(original['intent'])
                    if intent.get('action')=='material_chase' and intent.get('execution_channel')=='browser' and data['permit'].get('action_id')==original['id']:
                        data.update(isolated_readback=True,stop_requested=True,page=1,command=None,verify_cycle=data.get('verify_cycle',0)+1,
                            retry_at=(self.a.clock()+timedelta(minutes=5)).isoformat(),
                            message='原追投超过10分钟未核验，已转后台核验；保留预算和素材占用，其他独立调控按原规则继续，绝不重发本批。')
                        db.execute("UPDATE api_actions SET state='unknown',updated_at=? WHERE id=? AND state='submitting'",(self.now(),original['id']))
                        self.save(db,run,data)
                        self.audit(run['account_id'],'hybrid_readback_isolated',{'action_id':original['id'],'reserved_budget':str(amount(intent['body']['budget'])),
                            'material_ids':intent['material_ids'],'message':data['message'],'ad_changes':0})
                at=parse_time(data.get('last_progress_at',data.get('started_at',run['updated_at'])))
                if data.get('resumed_at'):at=max(at,parse_time(data['resumed_at']))
                if not data.get('preview') and not uncertain and not leased and (self.a.clock()-at).total_seconds()>600:
                    if action and action['state'] in {'draft','checking'}:
                        db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state IN ('draft','checking')",(self.now(),self.dump({'message':'超过10分钟没有读取或批次进展，未提交旧单，自动结束本轮','ad_changes':0}),data['action_id']))
                    data.update(command=None,message='超过10分钟无实际进展，已自动结束未提交轮次；其他巡航继续，下轮按原周期重扫。')
                    self.save(db,run,data,'cancelled');continue
                permit=data.get('permit')
                if data.get('action_id') and permit and permit.get('consumed') is False and self.a.clock()>=parse_time(permit['expires_at']):
                    db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state IN ('submitting','unknown')",(self.now(),self.dump({'message':'最终提交许可未被消费，已确认未授权点击；取消原单并重新核对。','ad_changes':0}),data['action_id']))
                    data.pop('action_id',None);data.pop('permit',None);data.pop('task_total',None)
                    data.update(phase='tasks',page=1,tasks={},lookups={},preflight=True,command=None,message='原提交许可未消费，未创建广告；重新核对预算。')
                    self.save(db,run,data,'cancelled' if data.get('stop_requested') else 'waiting')
                if data.get('preview') and (not data.get('action_id') or data.get('readback_only')) and (self.a.clock()-parse_time(data.get('started_at',run['updated_at']))).total_seconds()>1200:
                    data['message']='网页只读验证超过20分钟，已停止等待；原API规则保持不变。'
                    data['command']=None;self.save(db,run,data,'cancelled')
        # Network IO never runs under the writer/SQLite gate or on an extension
        # request. A single daemon reader shares the official rate governor.
        if api_control(self.a):self.api_lane.schedule();return
        if not self.api_scan_thread or not self.api_scan_thread.is_alive():
            with self.a.db() as db:
                candidate=db.execute("SELECT id FROM hybrid_runs WHERE state NOT IN ('completed','cancelled') AND json_extract(data,'$.phase')='api_materials' AND (json_extract(data,'$.retry_at') IS NULL OR json_extract(data,'$.retry_at')<=?) ORDER BY updated_at,id LIMIT 1",(self.now(),)).fetchone()
            if candidate:
                self.api_scan_thread=threading.Thread(target=self.scan_api_materials,args=(candidate['id'],),name='hybrid-api-materials',daemon=True)
                self.api_scan_thread.start()

    def api_active_snapshot(self,run,data,max_age=300):
        """Reuse existing complete API reads; never request another task list.

        `max_age` guards the candidate pool. The per-task material map is only used
        to fill rows the page itself cannot read, so the caller may accept an older
        read for that purpose instead of leaving the inventory blocked.
        """
        readings=[];saved=data.get('api_active_snapshot')
        if saved:readings.append(saved)
        if self.a.api.lock.acquire(blocking=False):
            try:
                w=self.a.api.workflows.get(run['account_id']+':'+run['plan_id'],{})
                if w.get('tasks_complete') is True and w.get('account_id')==run['account_id'] and w.get('plan_id')==run['plan_id']:
                    ids=set();valid=True;by_task={}
                    for t in w.get('tasks',[]):
                        if t.get('account_id')!=run['account_id'] or t.get('plan_id')!=run['plan_id']:valid=False;break
                        if t.get('status')=='PROCESSING' and t.get('scene')=='MATERIAL_ADD_BUDGET':
                            try:
                                mids=[exact_id(mid) for mid in t.get('material_ids',[])];ids.update(mids)
                                if t.get('id') is not None:by_task[exact_id(t['id'])]=mids
                            except ValueError:valid=False;break
                    if valid:readings.append({'kind':'complete_api_processing_materials','account_id':w['account_id'],'plan_id':w['plan_id'],
                        'date':w.get('date'),'captured_at':w.get('captured_at'),'material_ids':sorted(ids),'task_materials':by_task})
            finally:self.a.api.lock.release()
        fresh=[]
        for r in readings:
            try:
                if r.get('kind')=='complete_api_processing_materials' and r.get('account_id')==run['account_id'] and r.get('plan_id')==run['plan_id'] and r.get('date')==self.a.clock().date().isoformat() and 0<=(self.a.clock()-parse_time(r['captured_at'])).total_seconds()<=max_age:
                    if isinstance(r.get('material_ids'),list) and all(exact_id(mid)==mid for mid in r['material_ids']):fresh.append(r)
            except (ValueError,TypeError,KeyError):continue
        return copy.deepcopy(max(fresh,key=lambda r:parse_time(r['captured_at']))) if fresh else None

    def retire_api_draft(self,db,data):
        rid=data.get('action_id')
        if not rid:return
        row=db.execute('SELECT state FROM api_actions WHERE id=?',(rid,)).fetchone()
        if row and row['state'] not in {'draft','checking','cancelled','expired','rejected'}:raise ValueError('原单可能已提交，仅核验原单，不重新取数创建')
        if row and row['state'] in {'draft','checking'}:
            db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state IN ('draft','checking')",(self.now(),self.dump({'message':'重新读取API素材，旧未提交准备单已取消','ad_changes':0}),rid))
        data.pop('action_id',None);data.pop('command',None);data.pop('permit',None)

    def scan_api_materials(self,rid):
        try:
            with self.a.gate,self.lock,self.a.db() as db:
                run=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone()
                if not run or run['state'] in {'completed','cancelled'}:return
                data=json.loads(run['data'])
                if data['phase']!='api_materials':return
                account,profile,_=self.scope(run,data)
                self.retire_api_draft(db,data)
                active_snapshot=self.api_active_snapshot(run,data)
                data.update(message='API正在读取消耗门槛以上的素材，网页不扫描素材列表。',command=None)
                self.save(db,run,data)
            executor=getattr(self.a,'browser_executor',None)
            api_inventory=self.api_lane.read_inventory(run,data,profile) if api_control(self.a) else None
            if not api_control(self.a) and not active_snapshot and executor and executor.selected():
                try:
                    self.a.api.scan_workflow(account,profile,run['plan_id'],materials=False)
                    active_snapshot=self.api_active_snapshot(run,data)
                except (ValueError,OSError):
                    # Respect the normal API governor. Missing metadata stays
                    # unknown and can only be filled by the native inventory.
                    active_snapshot=None
            def progress():
                with self.a.gate,self.lock,self.a.db() as db:
                    latest=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone()
                    if not latest or latest['state'] in {'completed','cancelled'}:raise ValueError('本轮素材读取已停止')
                    current=json.loads(latest['data']);self.scope(latest,current)
                    if current['phase']!='api_materials':raise ValueError('本轮素材读取阶段已变化')
                    live=getattr(self.a.api,'workflow_progress',{}).get(account['id']+':'+run['plan_id'],{})
                    current.update(pages_read=live.get('page',0),materials_read=live.get('scanned_rows',0))
                    self.save(db,latest,current)
            for attempt in range(2):
                try:
                    workflow=self.a.api.scan_workflow(account,profile,run['plan_id'],materials=True,progress=progress,browser_candidates=True)
                    break
                except ValueError as exc:
                    if attempt or not str(exc).startswith(('素材分页总数变化','跨页素材ID重复')):raise
                    progress()
            with self.a.gate,self.lock,self.a.db() as db:
                latest=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone()
                if not latest or latest['state'] in {'completed','cancelled'}:return
                current=json.loads(latest['data']);self.scope(latest,current)
                if current['phase']!='api_materials':return
                if active_snapshot:current['api_active_snapshot']=active_snapshot
                start,end=date_range(self.a.clock(),profile['features']['f5'].get('lookback_days',5))
                if not workflow.get('eligible_range_complete') or (workflow.get('account_id'),workflow.get('plan_id'),workflow.get('date_start'),workflow.get('date_end'))!=(account['id'],run['plan_id'],start,end):raise ValueError('API素材范围尚未读取完整，不准备部分批次')
                batches=[]
                pools={};pool_ids={}
                for batch in workflow['batches']:
                    if batch.get('profile_digest')!=current['profile_digest'] or not batch.get('browser_candidate'):raise ValueError('API素材候选与本轮规则不符')
                    rule=profile['features']['f5']['rules'][batch['rule_index']]
                    fields=('total_budget','control_duration') if rule['chase_type']=='放量追投' else ('daily_budget','bid_value','optimization_target')
                    if any(rule.get(k) is None for k in fields):continue
                    index=batch['rule_index'];pool=pools.setdefault(index,[]);ids=pool_ids.setdefault(index,set())
                    for material in batch['materials']:
                        if material['id'] not in ids:pool.append(material);ids.add(material['id'])
                    batches.append({'material_ids':batch['material_ids'],'page':1,'rule_index':batch['rule_index'],
                        'date_start':start,'date_end':end,'selection_source':'official_api','materials':batch['materials'],
                        'scanned_at':workflow['materials_captured_at']})
                materials={m['id']:m for m in workflow['materials']}
                for remaining in workflow.get('unbatched_materials',[]):
                    index=remaining['rule_index'];mid=remaining['material_id']
                    if index in pools and mid not in pool_ids[index]:pools[index].append(materials[mid]);pool_ids[index].add(mid)
                current.update(phase='tasks',page=1,tasks={},lookups={},preflight=True,batches=batches,
                    api_pools=[{'rule_index':i,'materials':rows} for i,rows in sorted(pools.items())],
                    pages_read=workflow['pages_read'],materials_read=workflow['scanned_rows'],
                    material_captured_at=workflow['materials_captured_at'],eligible_range_complete=True,
                    unbatched_materials=len(workflow.get('unbatched_materials',[])),
                    message=f"API已扫描{workflow['scanned_rows']}条，筛出{len(batches)}批；网页将直接按素材ID准备追投。")
                skipped=set(current.get('unavailable_materials',[]))|set(current.get('submitted_materials',[]))|self.a.isolated_materials(account['id'],run['plan_id'])
                if not profile['features']['f5'].get('allow_running_materials',False):
                    skipped|=set(current.get('native_running_materials',[]))|set(current.get('api_running_materials',[]))
                if skipped:
                    current['batches']=self.regroup_api(current,profile,skipped)
                    current['message']=f"API已扫描{workflow['scanned_rows']}条，保留本轮已跳过素材记录后重组{len(current['batches'])}批；网页继续检查剩余候选。"
                if current.get('single_test'):current['batches']=current['batches'][:1]
                if api_inventory and current['batches']:self.api_lane.install(db,latest,current,profile,api_inventory)
                self.save(db,latest,current,'waiting' if current['batches'] and not current.get('finished') else 'completed')
            self.audit(account['id'],'hybrid_api_materials_ready',{'message':current['message'],'material_source':'official_api','scanned_rows':workflow['scanned_rows'],'batches':len(current['batches']),'ad_changes':0})
        except Exception as exc:
            with self.a.gate,self.lock,self.a.db() as db:
                latest=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone()
                if not latest or latest['state'] in {'completed','cancelled'}:return
                current=json.loads(latest['data'])
                if current['phase']!='api_materials':return
                delay=max(30,int(getattr(exc,'retry_after',60) or 60))
                current.update(command=None,retry_at=(self.a.clock()+timedelta(seconds=delay)).isoformat(),
                    message='API素材读取暂缓：'+str(exc)[:220]+'；本模式不会退回网页全量扫描。')
                self.save(db,latest,current)

    def bound(self,db,p,*,allow_expired_failure=False):
        run=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(p.get('run_id'),)).fetchone()
        if not run or run['state'] in {'completed','cancelled'}:raise ValueError('网页轮次已结束')
        data=json.loads(run['data']);command=data.get('command') or {}
        if p.get('id')!=command.get('id') or not secrets.compare_digest(str(p.get('lease','')),command.get('lease','')):raise ValueError('网页补位命令身份不符')
        if self.a.clock()>parse_time(command['expires_at']) and not allow_expired_failure:raise ValueError('网页读取命令已过期；不重复提交')
        self.scope(run,data)
        return run,data

    def observation(self,v,run,*,single_task=False,allow_stopped=False):
        required={'account_id','plan_id','path','group','captured_at','tab_id','document_id','page','page_size','total','has_next','rows','parent_budget','running','date_start','date_end','delivery_entry','parent_bid_type'}
        if not isinstance(v,dict) or set(v)!=required:raise ValueError('网页观察结构不完整')
        account=self.a.account(run['account_id'])
        if (v['account_id'],v['plan_id'],v['group'])!=(run['account_id'],run['plan_id'],account['tab_group']) or v['path'] not in {'/uni-prom/detail','/overall-prom/detail'}:raise ValueError('网页账户、母计划或入口不符')
        if type(v['tab_id']) is not int or v['tab_id']<=0 or not isinstance(v['document_id'],str) or not v['document_id']:raise ValueError('网页文档身份缺失')
        if not 0<=(self.a.clock()-parse_time(v['captured_at'])).total_seconds()<=90:raise ValueError('网页数据观察已过期')
        if type(v['page']) is not int or v['page']<1 or type(v['page_size']) is not int or not 1<=v['page_size']<=100:raise ValueError('页码或每页条数无效')
        if type(v['total']) is not int or not 0<=v['total']<=100000 or type(v['has_next']) is not bool or not isinstance(v['rows'],list) or len(v['rows'])>v['page_size']:raise ValueError('分页范围未核验')
        expected=min(v['page_size'],max(0,v['total']-(v['page']-1)*v['page_size']))
        if len(v['rows'])!=(1 if single_task and expected>0 else expected) or v['has_next']!=(v['page']*v['page_size']<v['total']):raise ValueError('页面行数、总数或翻页状态不一致')
        if v['running'] is not True and not (allow_stopped and v['running'] is False):raise ValueError('母计划未明确投放中')
        if v['delivery_entry'] not in {'chengfang','uni_promotion'} or v['parent_bid_type']!='SMART_BID_CUSTOM':raise ValueError('母计划产品入口或控成本方式未明确')
        amount(v['parent_budget'])
        return v

    def selected_observation(self,v,run,batch,*,profile):
        if not isinstance(v,dict) or v.get('selection_source')!='official_api':raise ValueError('缺少API素材按ID核对记录')
        if v.get('entry_source')=='home_plus':return self.home_observation(v,run,batch,profile=profile)
        base={k:value for k,value in v.items() if k not in {'selection_source','selected_materials'}}
        self.observation(base,run)
        if (v['date_start'],v['date_end'])!=(batch['date_start'],batch['date_end']):raise ValueError('本批API素材统计日期不符')
        if not 0<=(self.a.clock()-parse_time(batch['scanned_at'])).total_seconds()<=600:raise ValueError('API素材数据已过期，请重新读取后准备')
        selected=v.get('selected_materials')
        if not isinstance(selected,list) or len(selected)!=len(batch['material_ids']):raise ValueError('按ID核对的素材数量不一致')
        originals={r['id']:r for r in batch['materials']}
        seen=set();rule=profile['features']['f5']['rules'][batch['rule_index']]
        for row in selected:
            if not isinstance(row,dict) or set(row)!={'id','approved','selectable','chase_status','captured_at'}:raise ValueError('本批素材审核或追投状态未明确')
            mid=exact_id(row['id'])
            if mid in seen or mid not in originals or mid not in batch['material_ids']:raise ValueError('按ID核对的素材集合不一致')
            seen.add(mid)
            if type(row['approved']) is not bool or type(row['selectable']) is not bool or not isinstance(row['chase_status'],str) or not row['chase_status'].strip():raise ValueError('本批素材状态未加载')
            if not 0<=(self.a.clock()-parse_time(row['captured_at'])).total_seconds()<=90:raise ValueError('本批素材状态核对已过期')
            metrics=originals[mid]['metrics']
            proof={**row,'spend':str(metric(metrics['spend'])),'roi':None if metrics.get('roi') is None else str(metric(metrics['roi']))}
            if not matches(proof,rule,allow_running=profile['features']['f5'].get('allow_running_materials',False)):raise ValueError('提交前素材条件已变化')
        if seen!=set(batch['material_ids']):raise ValueError('按ID核对的素材集合不一致')
        return v

    def home_observation(self,v,run,batch,*,profile):
        data=json.loads(run['data'])
        if not home_entry_writer((data.get('command') or {}).get('extension_version')):raise ValueError('首页追投入口需要扩展1.11.0')
        direct=bool(isinstance(v.get('preflight'),dict) and v['preflight'].get('kind')=='direct_home_identity')
        # Keep historical detail provenance separate from the fresh native
        # home-row proof captured after the picker finished. Older adapters
        # retain their original 90-second source contract.
        try:renewed_home=direct and direct_home_writer(data.get('command') or {}) and tuple(map(int,data['command']['extension_version'].split('.')))>=(1,23,3)
        except (KeyError,ValueError):renewed_home=False
        source_seconds=180 if renewed_home else 90
        expected_keys={'entry_source','selection_source','preflight','binding','home','form_material_ids'}|({'picker_materials'} if direct else set())
        audience=batch.get('after',{}).get('audience')
        if audience:expected_keys.add('audience')
        if set(v)!=expected_keys:raise ValueError('首页追投观察结构不完整')
        if audience:
            proof=v['audience']
            if not isinstance(proof,dict) or set(proof)!={'gender','ages','exclude_fans','captured_at','source'} or proof.get('source')!='native_form':raise ValueError('本批缺少原生定向核对')
            from .chase_audience import validate
            if validate({k:proof[k] for k in ('gender','ages','exclude_fans')})!=audience or not 0<=(self.a.clock()-parse_time(proof['captured_at'])).total_seconds()<=90:raise ValueError('本批定向与已保存规则不符或已过期')
        pre=v['preflight']
        if not isinstance(pre,dict) or 'entry_source' in pre:raise ValueError('首页追投缺少原详情核对')
        if direct:
            if not direct_home_writer(data.get('command') or {}):raise ValueError('主页直投需要当前Ego执行器')
            keys={'kind','account_id','plan_id','group','path','parent_budget','running','delivery_entry','parent_bid_type','captured_at','tab_id','document_id'}
            if set(pre)!=keys or any(pre[k]!=expected for k,expected in [('account_id',run['account_id']),('plan_id',run['plan_id']),('group',self.a.account(run['account_id'])['tab_group'])]) or pre['running'] is not True:raise ValueError('主页直投母计划归属或状态不符')
            if type(pre['tab_id']) is not int or pre['tab_id']<=0 or not isinstance(pre['document_id'],str) or not pre['document_id'] or not 0<=(self.a.clock()-parse_time(pre['captured_at'])).total_seconds()<=source_seconds:raise ValueError('主页直投文档或时间无效')
            if pre['path'] not in {'/uni-prom/detail','/overall-prom/detail'} or pre['parent_bid_type']!='SMART_BID_CUSTOM' or pre['delivery_entry']!=data['delivery_entry'] or amount(pre['parent_budget'])!=amount(data['parent_budget']):raise ValueError('主页直投配置与预算清单不符')
            if not data.get('direct_inventory_complete') or not 0<=(self.a.clock()-parse_time(data['inventory_at'])).total_seconds()<=300:raise ValueError('在投素材清单未完整核验或已过期')
            if not 0<=(self.a.clock()-parse_time(batch['scanned_at'])).total_seconds()<=600:raise ValueError('本批API素材数据已过期')
            proofs=v.get('picker_materials');originals={m['id']:m for m in batch['materials']};seen=set();rule=profile['features']['f5']['rules'][batch['rule_index']]
            if not isinstance(proofs,list) or len(proofs)!=len(batch['material_ids']):raise ValueError('追投窗口素材审核数量不完整')
            active=set(data.get('direct_active_material_ids',[]))
            for proof in proofs:
                if not isinstance(proof,dict) or set(proof)!={'id','approved','selectable','captured_at'}:raise ValueError('追投窗口素材审核证据不完整')
                mid=exact_id(proof['id'])
                if mid in seen or mid not in originals or proof['approved'] is not True or proof['selectable'] is not True or not 0<=(self.a.clock()-parse_time(proof['captured_at'])).total_seconds()<=90:raise ValueError('追投窗口素材未审核通过、不可选或已过期')
                seen.add(mid);m=originals[mid]['metrics'];check={**proof,'spend':str(metric(m['spend'])),'roi':None if m.get('roi') is None else str(metric(m['roi'])),'chase_status':'调控中' if mid in active else '未追投'}
                if not matches(check,rule,allow_running=profile['features']['f5'].get('allow_running_materials',False)):raise ValueError('主页直投素材不符合原规则或已在投')
            if seen!=set(batch['material_ids']):raise ValueError('追投窗口素材名单不符')
        else:pre=self.selected_observation(pre,run,batch,profile=profile)
        b,h=v['binding'],v['home']
        bkeys={'account_id','plan_id','group','aweme_id','detail_url','captured_at','parent_budget','delivery_entry','parent_bid_type','tab_id','document_id'}
        hkeys={'account_id','plan_id','group','path','url','aweme_id','row_plan_id','row_aweme_id','parent_budget','running','parent_bid_type','delivery_entry','captured_at','tab_id','document_id'}
        if not isinstance(b,dict) or set(b)-{'aweme_source'}!=bkeys or not isinstance(h,dict) or set(h)!=hkeys:raise ValueError('首页对应关系或表单文档不完整')
        for value in (b,h):
            if any(value[k]!=pre[k] for k in ('account_id','plan_id','group','delivery_entry','parent_bid_type','tab_id')):raise ValueError('首页账户、母计划或原标签对应关系不符')
            if amount(value['parent_budget'])!=amount(pre['parent_budget']):raise ValueError('首页母计划预算已变化')
            limit=source_seconds if value is b else 90
            if not 0<=(self.a.clock()-parse_time(value['captured_at'])).total_seconds()<=limit:raise ValueError('首页母计划对应关系已过期')
        if b['document_id']!=pre['document_id'] or not isinstance(h['document_id'],str) or not h['document_id']:raise ValueError('首页缺少来源或实际表单文档')
        worker=(data.get('command') or {}).get('worker_id','')
        native_alias=isinstance(b.get('aweme_id'),str) and worker.startswith('ego:') and b.get('aweme_source')=='visible_detail' and re.fullmatch(r'zlp_\d{6,30}',b['aweme_id'])
        aweme=b['aweme_id'] if native_alias else exact_id(b['aweme_id'])
        source=b.get('aweme_source','visible_detail')
        if source not in {'visible_detail','official_plan_list'}:raise ValueError('首页抖音号来源无效')
        if source=='official_plan_list':
            command=data.get('command') or {};anchor=command.get('parent_anchor') or {}
            if not parent_anchor_writer(command.get('extension_version')) or anchor.get('kind')!='unique_official_plan_anchor' or (anchor.get('account_id'),anchor.get('plan_id'),anchor.get('aweme_id'))!=(run['account_id'],run['plan_id'],aweme):raise ValueError('缺少本命令已核验的官方计划抖音号对应关系')
            if not 0<=(self.a.clock()-parse_time(anchor['captured_at'])).total_seconds()<=600:raise ValueError('官方计划抖音号对应关系已过期')
        if h['aweme_id']!=aweme or h['running'] is not True:raise ValueError('首页抖音号或投放状态不符')
        if h['row_plan_id'] not in (None,run['plan_id']) or h['row_aweme_id'] not in (None,aweme) or not (h['row_plan_id']==run['plan_id'] or h['row_aweme_id']==aweme):raise ValueError('首页行未唯一对应本母计划')
        for url,path in ((b['detail_url'],pre['path']),(h['url'],h['path'])):
            if not isinstance(url,str):raise ValueError('首页来源网址无效')
            u=urlparse(url);q=parse_qs(u.query,keep_blank_values=True)
            if u.scheme!='https' or u.netloc!='qianchuan.jinritemai.com' or u.path!=path or q.get('aavid')!=[run['account_id']]:raise ValueError('首页或来源网址归属不符')
            if path in {'/uni-prom/detail','/overall-prom/detail'}:
                if q.get('adId')!=[run['plan_id']]:raise ValueError('来源母计划网址不符')
            elif path not in {'/uni-prom','/overall-prom'} or 'adId' in q and q['adId'] not in ([run['plan_id']],['']):raise ValueError('首页入口网址不符')
        if amount(h['parent_budget'])!=amount(data['parent_budget']):raise ValueError('首页母计划预算与本轮预算核验不符')
        ids=v['form_material_ids']
        if not isinstance(ids,list) or len(ids)!=len(batch['material_ids']) or any(not isinstance(mid,str) for mid in ids) or set(ids)!=set(batch['material_ids']):raise ValueError('首页表单素材集合不一致')
        return {**pre,'path':h['path'],'tab_id':h['tab_id'],'document_id':h['document_id'],'entry_source':'home_plus',
            'home_context':{'binding':b,'home':h}}

    def task_ids(self,db,run,data):
        owned=self.a.auto.owned(run['account_id'],run['plan_id'])
        stopped=set()
        for row in db.execute("SELECT intent FROM api_actions WHERE state='verified' AND json_extract(intent,'$.account_id')=? AND json_extract(intent,'$.plan_id')=? AND json_extract(intent,'$.action')='pause_task'",(run['account_id'],run['plan_id'])):
            stopped.add(json.loads(row['intent'])['task_id'])
        for row in db.execute("SELECT task_id FROM api_followups WHERE account_id=? AND plan_id=? AND state='verified'",(run['account_id'],run['plan_id'])):stopped.add(row['task_id'])
        # A currently running task overrides an older stop receipt.
        return owned-stopped-set(data['tasks'])-set(data['lookups'])-set(data.get('reserved_tasks',{}))

    def regroup_api(self,data,profile,excluded):
        from .chase_batching import groups
        cfg=profile['features']['f5'];start,end=date_range(self.a.clock(),cfg.get('lookback_days',5));batches=[];used=set()
        for pool in data.get('api_pools',[]):
            selected=[m for m in pool['materials'] if m['id'] not in excluded and m['id'] not in used]
            selected.sort(key=lambda m:(-metric(m['metrics']['spend']),m['id']))
            used.update(m['id'] for m in selected)
            for rows in groups(selected,cfg):
                batches.append({'material_ids':[m['id'] for m in rows],'materials':rows,'rule_index':pool['rule_index'],
                    'selection_source':'official_api','page':1,'date_start':start,'date_end':end,'scanned_at':data['material_captured_at']})
        return batches

    def skip_running_candidate(self,db,run,data,mid,profile,*,source='native',active_ids=(),reason=''):
        if source!='unavailable' and profile['features']['f5'].get('allow_running_materials',False):return False
        action=db.execute('SELECT intent,state FROM api_actions WHERE id=?',(data.get('action_id'),)).fetchone()
        batch=json.loads(action['intent'])['browser_batch'] if action else next(iter(data.get('batches',[])),{})
        if mid not in batch.get('material_ids',[]) or action and action['state']!='draft':return False
        if source=='native' and not set(active_ids)<=set(batch.get('material_ids',[])):return False
        if source=='unavailable':self.a.note_material_failure(run['account_id'],run['plan_id'],[mid],reason or '素材资格暂未完成核验',db=db)
        cancel_message=(f'本批未提交：仅跳过素材{mid}；保留其余合格候选补足批次。原因：{reason[:220] or "当前不可选"}' if source=='unavailable' else '本批有素材已在追投中，未提交；按原不重复规则重组其他候选。')
        if action:db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state='draft'",(self.now(),self.dump({'message':cancel_message,'ad_changes':0}),data['action_id']))
        data.pop('action_id',None)
        rejected=data.setdefault('unavailable_materials' if source=='unavailable' else 'api_running_materials' if source=='api' else 'native_running_materials',[])
        for value in ({mid}|set(active_ids)):
            if value not in rejected:rejected.append(value)
        excluded=set(data.get('unavailable_materials',[]))|set(data.get('native_running_materials',[]))|set(data.get('api_running_materials',[]))|set(data.get('submitted_materials',[]))|set(self.a.material_exclusions(run['account_id']))|self.a.deferred_materials(run['account_id'],run['plan_id'])|self.a.recent_chase_materials(run['account_id'],run['plan_id'],db=db)
        pools=data.get('api_pools')
        if not pools:
            by_rule={}
            for old in data.get('batches',[]):by_rule.setdefault(old['rule_index'],[]).extend(old.get('materials',[]))
            pools=[{'rule_index':i,'materials':rows} for i,rows in sorted(by_rule.items())];data['api_pools']=pools
        batches=self.regroup_api(data,profile,excluded)
        data.update(preflight=True,batches=batches,command=None,
            message=f'已按不重复追投设置跳过素材{mid}，保留其他API候选重组为{len(batches)}批；未提交。')
        if source=='api':data['message']=f'已复用本户API在投清单排除{len(rejected)}条在投候选，重组为{len(batches)}批；网页只核对剩余批次，未提交。'
        if source=='native' and len(set(active_ids))>1:data['message']=f'本批核对到{len(set(active_ids))}条素材已在追投，已统一跳过；保留其他API候选重组为{len(batches)}批，未提交。'
        if source=='unavailable':data['message']=f'仅跳过素材{mid}，其余候选保留并重组为{len(batches)}批；原因：{reason[:220] or "当前不可选"}；未提交。'
        for key in ('retry_at','auto_retry_paused','last_error'):data.pop(key,None)
        if not batches:
            from .chase_batching import batch_limits
            low,high=batch_limits(profile['features']['f5'])
            data['message']=f'素材{mid}'+('本轮未能完成资格核验，已暂缓' if source=='unavailable' else '当前在调控中，按设置排除')+f'；剩余候选不足最少{low}条（每批{low}–{high}条），本轮未提交。'
        else:
            # Rejecting one material does not change task budgets. Reuse only
            # a complete, recent inventory; the normal budget and final permit
            # checks still apply to the replacement batch.
            inventory_at=data.get('inventory_at')
            fresh=inventory_at and 0<=(self.a.clock()-parse_time(inventory_at)).total_seconds()<=180
            complete=data.get('task_total')==len(data.get('tasks',{})) and type(data.get('task_total')) is int
            if fresh and complete:self.after_tasks(db,run,data,profile)
            else:
                data.update(phase='tasks',page=1,tasks={},lookups={});data.pop('task_total',None)
        self.save(db,run,data,'waiting' if batches and not data.get('finished') else 'completed')
        if source=='unavailable':self.audit(run['account_id'],'hybrid_material_skipped',{'material_id':mid,'reason':reason[:250],
            'message':data['message'],'remaining_batches':len(batches),'ad_changes':0})
        return True

    def result(self,p):
        if not isinstance(p,dict) or set(p)!={'run_id','id','lease','ok','message','observation'} or type(p['ok']) is not bool:raise ValueError('网页回传字段无效')
        with self.a.gate,self.lock,self.a.db() as db:
            # A late failure can close only its exact, still-current read lease.
            # Late success/begin/final receipts still cannot authorize a write.
            run,data=self.bound(db,p,allow_expired_failure=p['ok'] is False)
            if not p['ok']:
                if data.get('material_source')=='official_api' and data['phase'] in {'prepare','dry_prepare'}:
                    active=re.findall(r'素材正在追投 ([1-9][0-9]{0,29})(?![0-9])',str(p['message']))
                    if active and self.skip_running_candidate(db,run,data,active[0],self.scope(run,data)[1],active_ids=active):return {'ok':True,'ad_changes':0,'continue_round':True}
                    unavailable=re.search(r'(?:(?:本批素材|添加视频)(?:审核未通过|左侧选择框已禁用) |本批素材ID搜索为空 |本批素材已删除 )\s*([1-9][0-9]{0,29})(?![0-9])',str(p['message']))
                    if unavailable and self.skip_running_candidate(db,run,data,unavailable[1],self.scope(run,data)[1],source='unavailable',reason=str(p['message'])):return {'ok':True,'ad_changes':0,'continue_round':True}
                    unresolved=re.search(r'(?:添加视频ID未在搜索结果唯一出现[：:]\s*|等待素材审核状态异步加载\s+|素材审核状态未明确\s+|等待素材追投状态异步加载\s+|素材追投状态未加载\s+)([1-9][0-9]{0,29})(?![0-9])',str(p['message']))
                    if unresolved and self.skip_running_candidate(db,run,data,unresolved[1],self.scope(run,data)[1],source='unavailable',reason='本轮暂缓核验未完成的单条素材：'+str(p['message'])):return {'ok':True,'ad_changes':0,'continue_round':True}
                failed_phase=data['phase'];prior=data.get('last_error') or {}
                if failed_phase in {'tasks','lookup','materials','dry_prepare'}:data.setdefault('read_stalled_at',self.now())
                version=(data.get('command') or {}).get('extension_version','unknown')
                attempt=prior.get('attempt',0)+1 if prior.get('phase')==failed_phase and prior.get('raw_message')==str(p['message'])[:300] and prior.get('extension_version')==version else 1
                failure,retry_at=self.failure(p['message'],attempt,self.a.clock(),failed_phase)
                failure['extension_version']=version
                data.update(last_error=failure,retry_at=retry_at,message=failure['title'],command=None)
                if attempt>=5:
                    if failed_phase=='verify':
                        data['auto_retry_paused']=False;data['retry_at']=(self.a.clock()+timedelta(minutes=5)).isoformat()
                        data['message']=failure['title']+'；仅继续只读核验原单，每5分钟重试，绝不重发。'
                    else:
                        data['auto_retry_paused']=True;data.pop('retry_at',None)
                        data['message']=failure['title']+'；本轮已暂停，10分钟无进展后自动结束并释放其他巡航。'
                if data['phase']=='prepare' and data.get('action_id'):
                    original=db.execute('SELECT intent FROM api_actions WHERE id=?',(data['action_id'],)).fetchone()
                    recoverable=any(s in str(p['message']) for s in ('表单素材集合不一致','提交前素材条件已变化','本批素材不在当前页','素材不能勾选','素材勾选未同步','素材勾选未跨搜索保留','本批素材审核','素材审核状态未明确','等待素材审核状态异步加载','等待素材追投状态异步加载','素材追投状态未加载','添加视频ID未在搜索结果唯一出现'))
                    if recoverable and original:
                        i=json.loads(original['intent']);note=self.a.note_material_failure(run['account_id'],run['plan_id'],i['material_ids'],str(p['message']),db=db)
                        data['message']=('本批连续核验失败，素材临时暂缓15分钟，继续其他合格素材；不将未知状态当作拒审。' if note['until_at'] else '本批核验未完成，重新读取后分组，不重试旧单。')+str(p['message'])[:180]
                    db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state='draft'",(self.now(),self.dump({'message':data['message']+'；本批未发出提交许可。','ad_changes':0}),data['action_id']))
                    data.pop('action_id',None);data.update(phase='tasks',page=1,tasks={},lookups={});data.pop('task_total',None)
                    if recoverable:
                        message=data['message'];self.reset_materials(data,message);data['preflight']=False
                        data['auto_retry_paused']=False;data.pop('retry_at',None)
                elif data['phase']=='verify' and data.get('action_id'):
                    db.execute("UPDATE api_actions SET state='unknown',updated_at=?,result=? WHERE id=? AND state='submitting'",(self.now(),self.dump({'phase':'browser_readback','message':'网页原提交结果待核验，不重复创建；'+data['message'],'ad_changes':0}),data['action_id']))
                    if failure['code']=='material_detail_pending' or any(v in str(p['message']) for v in ('无法翻到指定页','未找到指定页')):
                        data.update(page=1,verify_cycle=data.get('verify_cycle',0)+1)
                        data['message']='原任务读取位置已失效，自动返回第一页重新搜索原单；保留占用，绝不重复提交。'
                if failed_phase in {'prepare','dry_prepare'} and any(reason in str(p['message']) for reason in ('API素材数据已过期','本批素材数据已过期')):
                    self.retire_api_draft(db,data)
                    self.reset_materials(data,'准备期间API素材过期，旧未提交单已取消；重新取数后生成本批。')
                    data.pop('retry_at',None);data.pop('last_error',None);data.pop('read_stalled_at',None)
                if failed_phase in {'prepare','dry_prepare'} and any(reason in str(p['message']) for reason in ('本批素材不在当前页','本批日期或页码不符')):
                    data['stale_batch_restarts']=data.get('stale_batch_restarts',0)+1
                    self.reset_materials(data,'原批次页码或素材已变化，重新扫描后再准备；未提交。')
                    data['preflight']=False
                    if data['stale_batch_restarts']>=3:
                        data['auto_retry_paused']=True;data.pop('retry_at',None)
                        data['message']='素材位置连续变化3次，已暂停本轮；可重试当前阶段，未提交。'
                self.save(db,run,data)
                return {'ok':True,'ad_changes':0}
            supplied=p['observation']
            if data['phase']=='dom_probe':
                if not data['preview'] or not isinstance(supplied,dict) or set(supplied)!={'account_id','plan_id','group','path','url','captured_at','tab_id','document_id','rows'}:raise ValueError('只读诊断回传格式无效')
                v=supplied;u=urlparse(v['url']);q=parse_qs(u.query)
                if (v['account_id'],v['plan_id'],v['group'])!=(run['account_id'],run['plan_id'],self.a.account(run['account_id'])['tab_group']) or u.scheme!='https' or u.netloc!='qianchuan.jinritemai.com' or u.path!=v['path'] or q.get('aavid')!=[run['account_id']]:raise ValueError('只读诊断账户或来源不符')
                paths={'home':{'/uni-prom','/overall-prom'},'detail':{'/uni-prom/detail','/overall-prom/detail'}}
                if v['path'] not in paths[data['probe']['target']] or data['probe']['target']=='detail' and q.get('adId')!=[run['plan_id']]:raise ValueError('只读诊断页面类型不符')
                if type(v['tab_id']) is not int or v['tab_id']<=0 or not isinstance(v['document_id'],str) or not v['document_id'] or not 0<=(self.a.clock()-parse_time(v['captured_at'])).total_seconds()<=90:raise ValueError('只读诊断文档或时间无效')
                if not isinstance(v['rows'],dict) or set(v['rows'])!=set(data['probe']['selectors']) or any(not isinstance(a,list) or len(a)>8 for a in v['rows'].values()) or len(self.dump(v))>48000:raise ValueError('只读结构回传超出范围')
                data.update(probe_evidence=v,finished=True,command=None,message='只读页面结构已回传；未填写或提交广告。')
                self.save(db,run,data,'completed');return {'ok':True,'ad_changes':0}
            target_row=data['phase']=='verify' and isinstance(supplied,dict) and isinstance(supplied.get('rows'),list) and len(supplied['rows'])==1 and isinstance(supplied['rows'][0],dict) and 'name' in supplied['rows'][0]
            if data['phase']=='dry_prepare' and data.get('material_source')=='official_api':
                v=self.selected_observation(supplied,run,data['batches'][0],profile=self.scope(run,data)[1])
            else:v=self.observation(supplied,run,single_task=target_row,allow_stopped=data['phase']=='verify')
            _,profile,_=self.scope(run,data)
            data.pop('retry_at',None);data.pop('last_error',None);data.pop('read_stalled_at',None)
            data.pop('auto_retry_paused',None)
            phase=data['phase']
            if phase in {'tasks','lookup'}:
                rows={}
                for row in v['rows']:
                    direct=data.get('material_source')=='official_api' and direct_home_writer(data.get('command') or {}) and phase=='tasks'
                    if not isinstance(row,dict) or (set(row)-{'material_type'} if direct else set(row))!=({'id','budget','status','material_ids'} if direct else {'id','budget','status'}):raise ValueError('任务预算、状态或在投素材ID缺失')
                    if direct and (not isinstance(row['material_ids'],list) or len(row['material_ids'])>20 or len(set(row['material_ids']))!=len(row['material_ids']) or any(not isinstance(mid,str) or exact_id(mid)!=mid for mid in row['material_ids'])):raise ValueError('在投任务素材ID无效')
                    if direct and (row.get('material_type') not in (None,'VIDEO','LIVE_ROOM') or not row['material_ids'] and row.get('material_type')!='LIVE_ROOM' or row.get('material_type')=='LIVE_ROOM' and row['material_ids']):raise ValueError('在投任务素材类型与ID清单不符')
                    tid=exact_id(row['id']);amount(row['budget'])
                    if tid in rows or row['status'] not in TERMINAL|{'调控中','审核中'}:raise ValueError('任务重复或状态不明')
                    rows[tid]=row
                if phase=='tasks':
                    if v['page']!=data['page'] or any(r['status']!='调控中' for r in rows.values()):raise ValueError('请读取调控中任务完整列表')
                    if set(rows)&set(data['tasks']) or data.get('task_total',v['total'])!=v['total']:
                        data.update(tasks={},lookups={},page=1,command=None,message='在投任务分页变化，已从第一页重新核对，未提交。');data.pop('task_total',None)
                        self.save(db,run,data);return {'ok':True,'ad_changes':0}
                    data['tasks'].update(rows);data['task_total']=v['total'];data['parent_budget']=str(amount(v['parent_budget']))
                    data['delivery_entry']=v['delivery_entry'];data['parent_bid_type']=v['parent_bid_type']
                    if v['has_next']:data['page']+=1
                    else:
                        data['inventory_at']=v['captured_at']
                        if data.get('material_source')=='official_api' and direct_home_writer(data.get('command') or {}):
                            data['direct_inventory_complete']=True;data['direct_active_material_ids']=sorted({mid for task in data['tasks'].values() for mid in task['material_ids']})
                        self.after_tasks(db,run,data,profile)
                else:
                    tid=data['lookup_id']
                    owned=self.a.auto.owned(run['account_id'],run['plan_id'])
                    data['lookups'].update({key:value for key,value in rows.items() if key in owned})
                    if tid in rows:self.after_tasks(db,run,data,profile)
                    elif v['has_next'] and v['page']<5:
                        data.update(page=v['page']+1,command=None,message=f"原任务搜索未收敛，已核对第{v['page']}页；继续查找，未释放额度。")
                    else:
                        attempts=data.setdefault('lookup_attempts',{});attempts[tid]=attempts.get(tid,0)+1
                        if attempts[tid]>=2:
                            try:budget=self.a.known_task_budget(run['account_id'],run['plan_id'],tid)
                            except ValueError as exc:
                                data.update(finished=True,message=str(exc)+'；本轮追投暂缓，主巡航继续。')
                                self.save(db,run,data,'completed');return {'ok':True,'ad_changes':0}
                            data.setdefault('reserved_tasks',{})[tid]={'id':tid,'budget':str(budget),'status':'未核实保留额度'}
                            data.update(page=1,command=None,message=f'历史任务{tid}连续两次未对应，保留{budget}元预算占用，继续其他任务。')
                            self.after_tasks(db,run,data,profile)
                        else:data.update(page=1,command=None,verify_cycle=data.get('verify_cycle',0)+1,message=f'历史任务{tid}搜索返回{v["total"]}条但未对应，保留额度，刷新后再核对一次。')
                    data['command']=None
                    self.save(db,run,data,'completed' if data.get('finished') else 'waiting');return {'ok':True,'ad_changes':0}
            elif phase=='materials':
                start,end=date_range(self.a.clock(),profile['features']['f5'].get('lookback_days',5))
                if (v['date_start'],v['date_end'])!=(start,end) or v['page_size']!=100 or v['page']!=data['page']:raise ValueError('素材须为本户近几天、每页100条及当前指定页')
                rows=v['rows'];seen=set(data['seen_materials'])
                for row in rows:
                    if not isinstance(row,dict) or set(row)!={'id','name','spend','roi','chase_status','approved','selectable'}:raise ValueError('素材字段不完整')
                    exact_id(row['id']);metric(row['spend'])
                    if row['roi'] is not None:metric(row['roi'])
                    if row['id'] in seen:
                        # The page number can update before the asynchronous
                        # rows do. Keep prior complete pages and refresh this
                        # exact page rather than cycling page 1 indefinitely.
                        retries=data.get('page_reload_count',0)+1
                        data.update(page_reload_count=retries,verify_cycle=data.get('verify_cycle',0)+1,command=None,
                            message=f"第{data['page']}页仍含已读素材，已安排刷新当前页后重读；未提交。")
                        if retries>=3:
                            self.reset_materials(data,'素材翻页连续返回旧数据，已从第一页重新核对；未提交。')
                            data['retry_at']=(self.a.clock()+timedelta(seconds=60)).isoformat()
                        self.save(db,run,data);return {'ok':True,'ad_changes':0}
                    if type(row['approved']) is not bool or type(row['selectable']) is not bool or not isinstance(row['chase_status'],str):raise ValueError('审核或追投状态未明确')
                    seen.add(row['id'])
                spends=[metric(r['spend']) for r in rows]
                if spends!=sorted(spends,reverse=True) or spends and data.get('last_spend') is not None and spends[0]>metric(data['last_spend']):
                    self.reset_materials(data,'素材未按整体消耗降序或排序已变化，重新核对，未提交。');self.save(db,run,data);return {'ok':True,'ad_changes':0}
                data['page_reload_count']=0
                data['seen_materials']=list(seen);data['pages_read']+=1;data['materials_read']+=len(rows)
                if spends:data['last_spend']=str(spends[-1])
                from .chase_batching import groups
                used=set()
                for index,rule in enumerate(profile['features']['f5']['rules']):
                    if rule.get('enabled',True) is False:continue
                    excluded=set(self.a.material_exclusions(run['account_id']))
                    excluded.update(self.a.deferred_materials(run['account_id'],run['plan_id']))
                    if not profile['features']['f5'].get('allow_running_materials',False):
                        excluded.update(self.a.recent_chase_materials(run['account_id'],run['plan_id'],db=db))
                    selected=[r for r in rows if r['id'] not in excluded and r['id'] not in used and matches(r,rule,allow_running=profile['features']['f5'].get('allow_running_materials',False))]
                    for group in groups(selected,profile['features']['f5']):
                        if len(data['batches'])>=profile['safety']['max_actions_per_round']:break
                        used.update(r['id'] for r in group)
                        data['batches'].append({'material_ids':[r['id'] for r in group],'page':v['page'],'rule_index':index,'date_start':start,'date_end':end})
                floors=[metric(r['material_spend_min']) for r in profile['features']['f5']['rules'] if r.get('enabled',True) is not False and r.get('material_spend_min') is not None]
                finished=not v['has_next'] or bool(spends and floors and spends[-1]<min(floors))
                if finished:
                    data['message']=f"网页已扫{data['pages_read']}页、{data['materials_read']}条，命中{len(data['batches'])}批；本页不足整批素材保留，不当作已执行。"
                    if not data['batches']:self.save(db,run,data,'completed');return {'ok':True,'ad_changes':0}
                    if data.get('single_test'):data['batches']=data['batches'][:1]
                    if data['preview']:
                        data['phase']='dry_prepare';data['command']=None;self.save(db,run,data);return {'ok':True,'ad_changes':0}
                    data.update(phase='tasks',page=1,tasks={},lookups={},task_total=None,preflight=True)
                    data.pop('task_total')
                else:data['page']+=1
            elif phase=='verify':
                if target_row:self.verify_result(db,run,data,v)
                else:
                    for row in v['rows']:
                        if not isinstance(row,dict) or set(row)!={'id','budget','status'}:raise ValueError('原任务查找页字段无效')
                        exact_id(row['id']);amount(row['budget'])
                    if v['has_next']:
                        data['verify_pages_in_cycle']=data.get('verify_pages_in_cycle',0)+1
                        if data['verify_pages_in_cycle']>=3:
                            cycle=data.get('verify_cycle',0)+1;wait=self.verify_wait(cycle)
                            data.update(page=1,verify_pages_in_cycle=0,verify_cycle=cycle,
                                retry_at=(self.a.clock()+wait).isoformat(),message=f'本轮已核对3页仍未找到原单（第{cycle}轮），{int(wait.total_seconds()//60)}分钟后重新搜索；保留占用，不无限翻页或重发。')
                        else:data.update(page=v['page']+1,message=f"原任务尚未出现在第{v['page']}页，继续只读查找，不重复创建。")
                    else:
                        cycle=data.get('verify_cycle',0)+1;wait=timedelta(seconds=30) if cycle<12 else self.verify_wait(cycle)
                        data.update(page=1,verify_pages_in_cycle=0,verify_cycle=cycle,retry_at=(self.a.clock()+wait).isoformat(),message=f'已查完任务列表（第{cycle}轮），原提交尚未唯一回读；保留占用，{int(wait.total_seconds()//60)}分钟后继续核验，绝不重复创建。')
                        db.execute("UPDATE api_actions SET state='unknown',updated_at=?,result=? WHERE id=? AND state IN ('submitting','unknown','submitted_pending_verification')",(self.now(),self.dump({'phase':'browser_readback','message':data['message'],'ad_changes':0}),data['action_id']))
            elif phase=='dry_prepare':
                if not data['preview']:raise ValueError('当前不是表单预演')
                data['preview_entry_message']=str(p['message'])[:300]
                if data.get('material_source')=='official_api':
                    data['preview_evidence']={k:v[k] for k in ('account_id','plan_id','date_start','date_end','captured_at','selection_source','selected_materials','tab_id','document_id')}
                    for k in ('entry_source','home_context','path'):
                        if k in v:data['preview_evidence'][k]=v[k]
                data['message']='网页读取及首批表单预演已完成，已取消表单，未提交广告。'
                data['finished']=True
            else:raise ValueError('当前阶段不能接收此类观察')
            data['command']=None;self.save(db,run,data,'completed' if data.get('finished') else 'waiting')
        return {'ok':True,'ad_changes':0}

    @staticmethod
    def verify_wait(cycle):
        """A readback that never matches must slow down instead of scanning forever.

        Nothing about the original action changes: it stays unknown, its budget and
        material occupancy stay reserved, and no new submission is made.
        """
        if cycle>=48:return timedelta(minutes=60)
        if cycle>=24:return timedelta(minutes=30)
        if cycle>=12:return timedelta(minutes=10)
        return timedelta(minutes=5)

    @staticmethod
    def reset_materials(data,message):
        data.update(phase='api_materials' if data.get('material_source')=='official_api' else 'materials',page=1,command=None,batches=[],seen_materials=[],pages_read=0,materials_read=0,message=message)
        data.pop('last_spend',None)

    def after_tasks(self,db,run,data,profile):
        if data.get('material_source')=='official_api':
            if data.get('direct_inventory_complete') and not profile['features']['f5'].get('allow_running_materials',False):
                excluded=set(data.get('direct_active_material_ids',[]))|set(data.get('unavailable_materials',[]))|set(data.get('submitted_materials',[]))|set(self.a.material_exclusions(run['account_id']))|self.a.deferred_materials(run['account_id'],run['plan_id'])|self.a.recent_chase_materials(run['account_id'],run['plan_id'],db=db)
                data['batches']=self.regroup_api(data,profile,excluded)
                if not data['batches']:data.update(finished=True,message='API候选经在投任务清单去重后不足完整一批；未搜索素材页，未提交。');return
            # A complete native active list plus conservative reservation for
            # historical tasks avoids a slow sequence of name-search lookups.
            if not data['preview']:
                for tid in sorted(self.task_ids(db,run,data)):
                    try:budget=self.a.known_task_budget(run['account_id'],run['plan_id'],tid)
                    except ValueError as exc:
                        data.update(finished=True,message=str(exc)+'；未释放该任务额度，本轮暂缓。');return
                    data.setdefault('reserved_tasks',{})[tid]={'id':tid,'budget':str(budget),'status':'未核实保留额度'}
            if not 0<=(self.a.clock()-parse_time(data['material_captured_at'])).total_seconds()<=600:
                self.reset_materials(data,'API素材数据超过10分钟，重新读取后准备本批。');return
            if data['preview']:
                data['phase']='dry_prepare';return
            try:
                if self.prepare(db,run,data,profile) is False:return
            except ValueError as exc:
                data.update(finished=True,message='本轮未提交，暂缓追投并释放其他巡航：'+str(exc)[:220]);return
            data['phase']='prepare';return
        if data['preview']:
            # A cancelled form rehearsal cannot consume money. Historical
            # ownership is checked separately before any real browser batch.
            data.update(phase='materials',page=1);return
        missing=sorted(self.task_ids(db,run,data))
        if missing:data.update(phase='lookup',lookup_id=missing[0],page=1);return
        if (self.a.clock()-parse_time(data['inventory_at'])).total_seconds()>180:
            data.update(phase='tasks',page=1,tasks={});data.pop('task_total',None);return
        if data.get('preflight'):
            try:
                if self.prepare(db,run,data,profile) is False:return
            except ValueError as exc:
                data.update(finished=True,message='本轮未提交，暂缓追投并释放其他巡航：'+str(exc)[:220]);return
            data['phase']='prepare'
        else:data.update(phase='materials',page=1)

    def budget_check(self,run,data,budget,*,cost_control=False):
        grant=self.a.auto.row(run['account_id']);owned=self.a.auto.owned(run['account_id'],run['plan_id'])
        tasks={**data.get('reserved_tasks',{}),**data['lookups'],**data['tasks']}
        tasks={t['id']:t for t in self.a.budget_tasks_with_holds(run['account_id'],run['plan_id'],list(tasks.values()))}
        active={'调控中','审核中','未核实保留额度','PROCESSING'}
        total=sum((amount(t['budget']) for tid,t in tasks.items() if tid in owned and t.get('status','未核实保留额度') in active),Decimal(0))
        reserve=self.a.isolated_budget(run['account_id'],run['plan_id'])
        if not grant.get('budget_unlimited') and total+reserve+budget>amount(grant['budget_cap']):raise ValueError('API与网页共用额度：本批及后台核验保留额将超过在投预算上限')
        parent_total=sum((amount(t['budget']) for t in tasks.values() if t.get('status','未核实保留额度') in active),Decimal(0))
        if parent_total+reserve+budget>amount(data['parent_budget'])*Decimal('.3'):raise ValueError('本批及后台核验保留额将超过母计划预算30%')
        if cost_control:
            with self.a.db() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='hybrid_rule_runs'").fetchone():
                    row=db.execute('SELECT data FROM hybrid_rule_runs WHERE account_id=? AND plan_id=? ORDER BY updated_at DESC LIMIT 1',(run['account_id'],run['plan_id'])).fetchone()
                    if row:
                        from .hybrid_rules import quota_amount
                        observed=json.loads(row['data']);values=[]
                        for s in observed.get('rows',{}).values():
                            ctx=s.get('context',{})
                            if ctx.get('native_available_budget') is not None and 0<=(self.a.clock()-parse_time(s['captured_at'])).total_seconds()<=90:values.append(quota_amount(ctx['native_available_budget']))
                        if values and budget>min(values):raise ValueError('平台可追加预算不足，本批未开追投表单；等待已启用的暂停规则释放额度后再读数')

    def prepare(self,db,run,data,profile):
        if data.get('single_test') and data['completed']>=1:raise ValueError('单条测试已完成，不再新建')
        if data.get('action_id'):return
        deferred=self.a.deferred_materials(run['account_id'],run['plan_id'])
        data['batches']=[b for b in data['batches'] if not set(b['material_ids'])&deferred]
        if not profile['features']['f5'].get('allow_running_materials',False):
            recent=self.a.recent_chase_materials(run['account_id'],run['plan_id'],db=db)
            before=len(data['batches'])
            data['batches']=[b for b in data['batches'] if not set(b['material_ids'])&recent]
            data['duplicate_batches_skipped']=data.get('duplicate_batches_skipped',0)+before-len(data['batches'])
        if not data['batches']:
            if data.get('duplicate_rescans',0)<1:
                data['duplicate_rescans']=data.get('duplicate_rescans',0)+1
                self.reset_materials(data,'重复或临时暂缓素材已排除，重新扫描并分组其他合格素材；未提交旧批次。')
                data['preflight']=False
            else:data.update(finished=True,message='本轮剩余批次均为重复或暂缓素材，已跳过；下个原定巡航周期继续。')
            return False
        candidate=data['batches'][0];rule=profile['features']['f5']['rules'][candidate['rule_index']]
        from .chase_batching import batch_limits
        low,high=batch_limits(profile['features']['f5'])
        if not low<=len(candidate['material_ids'])<=high:raise ValueError('本批素材数量不在已保存区间内')
        budget=amount(rule['total_budget'] if rule['chase_type']=='放量追投' else rule['daily_budget'])
        self.budget_check(run,data,budget,cost_control=rule['chase_type']!='放量追投')
        if data['completed']>=profile['safety']['max_actions_per_round']:raise ValueError('本轮已达到单轮动作上限')
        self.a.require_allowed_materials(run['account_id'],candidate['material_ids'])
        rid=secrets.token_hex(12);name='QCX_'+self.a.clock().strftime('%m%d_')+rid[:16]
        after={k:rule[k] for k in (('chase_type','total_budget','control_duration') if rule['chase_type']=='放量追投' else ('chase_type','daily_budget','bid_value','optimization_target'))}
        from .chase_audience import configured,effective
        if configured(rule):after['audience']=effective(rule)
        intent={'account_id':run['account_id'],'plan_id':run['plan_id'],'parent_plan_id':run['plan_id'],'target_kind':'control_task','action':'material_chase','execution_channel':'browser',
            'reason':'API与网页共用本户F5规则及额度；'+data['reason'],'material_ids':candidate['material_ids'],
            'auto_authority':{'grant_id':data['grant_id']},'profile_revision':data['revision'],'profile_digest':data['profile_digest'],
            'browser_batch':{**candidate,'after':after,'task_name':name,'allow_running_materials':profile['features']['f5'].get('allow_running_materials',False)},'body':{'budget':float(budget),'smart_bid_type':'SMART_BID_CONSERVATIVE' if rule['chase_type']=='放量追投' else 'SMART_BID_CUSTOM'},
            'delivery_entry':data['delivery_entry'],'parent_bid_type':data['parent_bid_type'],
            'after':{'daily_budget':str(budget) if rule['chase_type']=='控成本追投' else None,'total_budget':str(budget) if rule['chase_type']=='放量追投' else None,
                'optimization_target':rule.get('optimization_target'),'bid':rule.get('bid_value') if rule.get('optimization_target')=='直播间成交' else None,
                'roi2_goal':rule.get('bid_value') if rule.get('optimization_target')!='直播间成交' else None},'notes':['网页补位，一次提交；与API共用额度、去重和日志。']}
        if api_control(self.a):
            from .official_contracts import chase_contract
            intent['api_readback_contract']=chase_contract(rule,(data.get('api_inventory') or {}).get('roi_basis'),delivery_entry=data['delivery_entry'],for_browser_readback=True)['body']
        now=self.now();expires=(self.a.clock()+timedelta(minutes=15)).isoformat()
        if data.get('single_test'):intent['test_run_id']=run['id']
        guard=self.a._guard(intent,self.a.account(run['account_id']))
        if not guard['allowed']:raise ValueError('；'.join(guard['blocking_reasons']))
        intent['cost_guarantee']=guard
        db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,NULL)',(rid,'draft',self.dump(intent),digest(intent),now,expires,now))
        db.execute('INSERT INTO api_auto_attempts VALUES(?,?,?,?)',(rid,run['account_id'],data['grant_id'],self.a.clock().date().isoformat()))
        data['action_id']=rid

    def begin(self,p):
        if not isinstance(p,dict) or set(p)!={'run_id','id','lease','action_id','observation'}:raise ValueError('网页提交许可字段无效')
        self.a.conflict_check()
        with self.a.gate,self.lock,self.a.db() as db:
            run,data=self.bound(db,p)
            if data['preview'] or data['phase']!='prepare' or p['action_id']!=data.get('action_id'):raise ValueError('本轮尚未批准提交')
            if not current_writer(data['command'].get('extension_version')):raise ValueError('本批需要扩展1.8.0，未发出提交许可')
            row=db.execute('SELECT * FROM api_actions WHERE id=?',(data['action_id'],)).fetchone()
            if not row or row['state']!='draft':raise ValueError('本批提交许可已消费，不再提交')
            if self.a.clock()>=parse_time(row['expires_at']):raise ValueError('本批已过准备有效期，未提交')
            if self.pending(db,run['account_id'],run['plan_id'],row['id']):raise ValueError('原母计划仍有其他待处理单据')
            intent=json.loads(row['intent']);batch=intent['browser_batch']
            api_selected=batch.get('selection_source')=='official_api'
            if api_selected:
                if not api_material_writer(data['command'].get('extension_version')):raise ValueError('API取数网页追投需要扩展1.10.0')
                v=self.selected_observation(p['observation'],run,batch,profile=self.scope(run,data)[1])
            else:v=self.observation(p['observation'],run)
            self.a.require_allowed_materials(run['account_id'],intent['material_ids'])
            _,profile,_=self.scope(run,data);batch=intent['browser_batch'];rule=profile['features']['f5']['rules'][batch['rule_index']]
            if not api_selected:
                if (v['date_start'],v['date_end'],v['page'],v['page_size'])!=(batch['date_start'],batch['date_end'],batch['page'],100):raise ValueError('本批统计日期或页码已变化')
                rows={r['id']:r for r in v['rows']}
                if any(mid not in rows or not matches(rows[mid],rule,allow_running=profile['features']['f5'].get('allow_running_materials',False)) for mid in intent['material_ids']):raise ValueError('提交前素材条件已变化')
            if not 0<=(self.a.clock()-parse_time(data['inventory_at'])).total_seconds()<=300:raise ValueError('在投任务预算核验已过期，未提交')
            self.budget_check(run,data,amount(intent['body']['budget']),cost_control=intent['body'].get('smart_bid_type')=='SMART_BID_CUSTOM')
            self.a.auto.verify_scope(intent,exclude=row['id'])
            guard=self.a._guard(intent,self.a.account(run['account_id']))
            if not guard['allowed']:raise ValueError('；'.join(guard['blocking_reasons']))
            home=v.get('home_context',{}).get('home',{})
            data['submission_scope']={'account_id':run['account_id'],'plan_id':run['plan_id'],'entry_source':v.get('entry_source','detail'),
                'row_plan_id':home.get('row_plan_id'),'aweme_id':home.get('aweme_id'),'checked_at':self.now()}
            if api_control(self.a):
                data['submission_materials']={'source':'validated_native_form','material_ids':list(intent['material_ids']),'checked_at':self.now()}
            db.execute("UPDATE api_actions SET state='submitting',updated_at=?,result=? WHERE id=?",(self.now(),self.dump({'phase':'browser_submission','message':'网页已获得一次提交许可，等待独立结果回读。','submission_scope':data['submission_scope'],'ad_changes':0}),row['id']))
            token=secrets.token_hex(24)
            data['permit']={'token':token,'action_id':row['id'],'consumed':False,'expires_at':data['command']['expires_at'],'extension_version':data['command'].get('extension_version')}
            data['submitted_document_id']=v['document_id'];data['begin_at']=self.now()
            data['phase']='verify';data['page']=1;data['command']=None;self.save(db,run,data)
        return {'ok':True,'submit_once':True,'submit_token':token}

    def final(self,p):
        if not isinstance(p,dict) or set(p)!={'run_id','action_id','submit_token'}:raise ValueError('最终提交许可字段无效')
        self.a.conflict_check()
        with self.a.gate,self.lock,self.a.db() as db:
            run=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(p['run_id'],)).fetchone()
            if not run:raise ValueError('网页轮次不存在')
            data=json.loads(run['data']);permit=data.get('permit') or {}
            if permit.get('action_id')!=p['action_id'] or permit.get('consumed') is not False or not secrets.compare_digest(str(p['submit_token']),permit.get('token','')):raise ValueError('最终许可已消费或身份不符')
            if self.a.clock()>=parse_time(permit['expires_at']):raise ValueError('最终提交许可过期')
            if not current_writer(permit.get('extension_version')):raise ValueError('旧版网页许可不可继续点击，请加载1.8.0后只核验原单')
            row=db.execute('SELECT * FROM api_actions WHERE id=?',(p['action_id'],)).fetchone()
            if not row or row['state']!='submitting':raise ValueError('原提交状态已变化')
            intent=json.loads(row['intent'])
            if data.get('stop_requested'):raise ValueError('本轮已停止，未发出最终点击许可')
            if intent['browser_batch'].get('selection_source')=='official_api' and not 0<=(self.a.clock()-parse_time(intent['browser_batch']['scanned_at'])).total_seconds()<=600:raise ValueError('API素材数据已过期，未发出最终点击许可')
            self.a.require_allowed_materials(run['account_id'],intent['material_ids'])
            self.a.auto.verify_scope(intent,exclude=row['id'])
            if self.pending(db,run['account_id'],run['plan_id'],row['id']):raise ValueError('原母计划已有其他未处理单据')
            if self.mode(run['account_id'])=='api_only':raise ValueError('网页补位已关闭')
            permit['consumed']=True;self.save(db,run,data)
        return {'ok':True,'allow':True}

    def verify_result(self,db,run,data,v,*,api=False):
        rid=data.get('action_id');row=db.execute('SELECT * FROM api_actions WHERE id=?',(rid,)).fetchone()
        readonly=data.get('readback_only') is True
        if not row or row['state'] not in ({'verified'} if readonly else {'submitting','unknown','submitted_pending_verification'}):raise ValueError('原提交记录已变化')
        intent=json.loads(row['intent']);expected=intent['browser_batch'];rows=v['rows']
        if api:
            if (v.get('source'),v.get('account_id'),v.get('plan_id'))!=('official_api',run['account_id'],run['plan_id']):raise ValueError('API回读来源或归属不符')
        elif v['document_id']==data.get('submitted_document_id'):raise ValueError('请刷新后独立读取原创建结果')
        if parse_time(v['captured_at'])<=parse_time(data['begin_at']):raise ValueError('独立回读时间未晚于原提交')
        if len(rows)!=1:raise ValueError('未找到唯一原任务，保持待核验，绝不重复创建')
        task=rows[0]
        if set(task)!={'id','name','material_ids','after','status'} or task['name']!=expected['task_name'] or sorted(task['material_ids'])!=sorted(intent['material_ids']):raise ValueError('回读的任务名称或素材集合不一致')
        exact_id(task['id'])
        if task['after']!=expected['after']:raise ValueError('回读的追投预算、方式或目标不一致')
        if readonly:
            if task['id']!=json.loads(row['result'])['task_id']:raise ValueError('回读的实际任务ID与原单不一致')
            data.update(finished=True,readback_result={'task_id':task['id'],'independently_verified':True,'ad_changes':0},
                message='原任务 '+task['id']+' 已由程序重新核对任务ID、素材、预算和目标；本轮只读，未创建新追投。')
            return
        result={'phase':'browser_readback','message':'网页任务ID、素材、预算及目标已独立回读核验。','task_id':task['id'],'task_status':task['status'],'independently_verified':True,
            'verified_parent_plan_id':run['plan_id'],'submission_scope':data.get('submission_scope'),'ad_changes':1}
        if api:result.update(phase='api_readback',message='网页新建追投，API已核验原任务ID、预算及目标；素材按回读来源留痕。',readback_source='official_api',materials_verification_source=v.get('materials_source','official_api'))
        if expected.get('after',{}).get('audience'):result.update(audience=expected['after']['audience'],audience_verification='pre_submit_native_form',audience_api_checks=v.get('audience_api_checks',{}))
        db.execute("UPDATE api_actions SET state='verified',updated_at=?,result=? WHERE id=?",(self.now(),self.dump(result),rid))
        data['completed']+=1;data['batches'].pop(0);data.pop('action_id',None)
        for key in ('permit','submission_materials','submission_scope','submitted_document_id','begin_at'):data.pop(key,None)
        data.setdefault('submitted_materials',[]).extend(intent['material_ids'])
        db.execute("UPDATE api_auto SET state='running',message=? WHERE account_id=? AND enabled=1 AND grant_id=?",(f'网页追投已实际创建并回读{data["completed"]}批；按原规则继续。',run['account_id'],data['grant_id']))
        data.update(phase='api_inventory' if api_control(self.a) else 'tasks',page=1,tasks={},lookups={},preflight=True)
        data.pop('task_total',None)
        if data.get('rule_yield_after_batch') is not None and data['completed']>=data['rule_yield_after_batch']:
            data.pop('rule_yield_after_batch',None)
            data.update(yielding=True,rule_yield_queued_until=(self.a.clock()+timedelta(seconds=30)).isoformat(),
                message='本批已创建并核验，先处理已等待的主巡航；保留后续素材候选。')
            db.execute('UPDATE api_rule_auto SET next_at=? WHERE account_id=? AND plan_id=? AND enabled=1',(self.now(),run['account_id'],run['plan_id']))
        if data.get('exclusions_dirty'):
            data.pop('exclusions_dirty',None);self.reset_materials(data,'原提交已核验，按更新后的排除列表重新筛选。');data['preflight']=False
        if not data['batches'] or data.get('single_test') or data.get('stop_requested'):
            data['finished']=True;data['message']='单条测试已创建并独立核验，已停止后续追投。' if data.get('single_test') else f"网页补位已完成并核验{data['completed']}批，已停止新建。"
        if data.get('isolated_readback'):
            data['finished']=True;data['message']='后台原单已独立核验，保留额度按实际任务重新计算；本轮只核验原单，没有重复追投。'
