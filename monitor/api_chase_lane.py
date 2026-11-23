"""Official task reads and receipts; Ego only creates the selected F5 batch."""
import copy
import json
import threading
from contextlib import nullcontext
from datetime import timedelta
from .actuation import amount
from .execution_strategy import api_control
from .official_api import exact_id
from .official_workflow import fresh_parent,TASK_FIELDS
from .official_monitoring import pages
from .official_contracts import chase_contract
from .official_rate import prioritize_readback
from .schema import parse_time

API_PHASES={'api_materials','api_inventory','verify'}

class APIChaseLane:
    def __init__(self,hybrid):self.h=hybrid;self.a=hybrid.a

    def known_materials(self,account_id,plan_id,task_id):
        mappings=set()
        with self.a.db() as db:
            for row in db.execute("SELECT intent,result FROM api_actions WHERE state='verified' AND json_extract(intent,'$.account_id')=? AND json_extract(intent,'$.plan_id')=? AND json_extract(intent,'$.action')='material_chase'",(account_id,plan_id)):
                i=json.loads(row['intent']);r=json.loads(row['result'] or '{}')
                if str(r.get('task_id') or r.get('reply',{}).get('id'))!=task_id:continue
                mids=i.get('material_ids')
                if isinstance(mids,list) and 0<len(mids)<=20 and len(set(mids))==len(mids) and all(exact_id(m)==m for m in mids):mappings.add(tuple(sorted(mids)))
        if len(mappings)>1:raise ValueError('原任务已核验素材映射冲突，等待API明确返回')
        return list(next(iter(mappings))) if mappings else None

    def fresh(self,value,run,seconds=180):
        try:return (value.get('source')=='official_api' and value.get('complete') is True
            and (value['account_id'],value['plan_id'],value['date'])==(run['account_id'],run['plan_id'],self.a.clock().date().isoformat())
            and 0<=(self.a.clock()-parse_time(value['captured_at'])).total_seconds()<=seconds)
        except (KeyError,TypeError,ValueError):return False

    def read_inventory(self,run,data,profile):
        account=self.a.account(run['account_id']);api=self.a.api
        saved=data.get('api_inventory') or {}
        if self.fresh(saved,run):return copy.deepcopy(saved)
        with api.lock:workflow=copy.deepcopy(api.workflows.get(account['id']+':'+run['plan_id'],{}))
        at=self.a.clock()
        try:cached=(workflow.get('tasks_complete') is True and workflow.get('date')==at.date().isoformat()
            and 0<=(at-parse_time(workflow['captured_at'])).total_seconds()<=180)
        except (KeyError,ValueError,TypeError):cached=False
        if not cached:workflow=api.scan_workflow(account,profile,run['plan_id'],materials=False)
        parent=fresh_parent(api,account,run['plan_id'])
        if (workflow.get('account_id'),workflow.get('plan_id'),workflow.get('date'))!=(account['id'],run['plan_id'],at.date().isoformat()) or workflow.get('tasks_complete') is not True:
            raise ValueError('API在投任务清单范围未完整核验，不安排网页读取')
        tasks={};active=set();goals=set()
        if not isinstance(workflow.get('tasks'),list):raise ValueError('API任务清单缺字段')
        for t in workflow['tasks']:
            if (t.get('account_id'),t.get('plan_id'))!=(account['id'],run['plan_id']) or t.get('status')!='PROCESSING':raise ValueError('API任务归属或状态不符')
            if t.get('scene')!='MATERIAL_ADD_BUDGET':continue
            tid=exact_id(t.get('id'));budget=amount(t.get('budget'))
            if tid in tasks:raise ValueError('API任务ID重复，未准备追投')
            mids=t.get('material_ids')
            if not isinstance(mids,list) or len(mids)>20 or len(set(mids))!=len(mids) or any(exact_id(m)!=m for m in mids):raise ValueError('API在投任务素材清单不完整')
            source='official_api'
            if t.get('material_type')=='LIVE_ROOM':mids=[]
            elif not mids:
                known=self.known_materials(account['id'],run['plan_id'],tid)
                if known:mids=known;source='verified_creation_ledger_with_api_status'
                elif not profile['features']['f5'].get('allow_running_materials',False):raise ValueError('API任务'+tid+'未返回素材ID且无已核验创建映射，无法排除在投素材；等待API，不展开网页任务')
            active.update(mids)
            if t.get('roi2_goal') is not None and t.get('deep_external_action_name') in {'支付ROI','净成交ROI'}:goals.add(t['deep_external_action_name'])
            tasks[tid]={'id':tid,'budget':str(budget),'status':'调控中','material_ids':mids,'material_type':'VIDEO' if mids else 'LIVE_ROOM' if t.get('material_type')=='LIVE_ROOM' else 'UNKNOWN','material_mapping_source':source}
        value={'source':'official_api','complete':True,'account_id':account['id'],'plan_id':run['plan_id'],'date':workflow['date'],
            'captured_at':workflow['captured_at'],'tasks':tasks,'active_material_ids':sorted(active),
            'parent_budget':str(amount(parent.get('budget'))),'delivery_entry':parent['delivery_entry'],
            'parent_bid_type':parent.get('smart_bid_type'),'roi_basis':next(iter(goals)) if len(goals)==1 else None}
        if not self.fresh(value,run):raise ValueError('API在投任务清单已过期，等待API刷新')
        if value['parent_bid_type']!='SMART_BID_CUSTOM':raise ValueError('API母计划未明确为控成本计划')
        return value

    def install(self,db,run,data,profile,value):
        if not self.fresh(value,run):
            data.update(phase='api_inventory',command=None,message='等待API刷新在投任务与预算；网页不遍历任务');return
        data.update(api_inventory=copy.deepcopy(value),inventory_source='official_api',inventory_at=value['captured_at'],
            direct_inventory_complete=True,direct_active_material_ids=value['active_material_ids'],tasks=copy.deepcopy(value['tasks']),
            task_total=len(value['tasks']),lookups={},parent_budget=value['parent_budget'],delivery_entry=value['delivery_entry'],
            parent_bid_type=value['parent_bid_type'],preflight=True,command=None)
        self.h.after_tasks(db,run,data,profile)
        if not data.get('finished'):data['message']='API已完成在投任务、预算和素材去重；智能体只准备本批追投'

    def migrate(self,db,run,data):
        """No old browser list command may escape after the strategy changes."""
        if not api_control(self.a):return False
        if data.get('phase') in API_PHASES:return False
        action=db.execute('SELECT state FROM api_actions WHERE id=?',(data.get('action_id'),)).fetchone()
        if action and action['state'] in {'submitting','unknown','submitted_pending_verification'}:
            data.update(phase='verify',command=None,message='仅通过API核验原追投，不再浏览任务列表')
        elif data.get('material_source')!='official_api':
            if action:
                db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=? AND state IN ('draft','checking')",(self.h.now(),data['action_id']))
                data.pop('action_id',None)
            data.update(material_source='official_api',phase='api_materials',command=None,yielding=False,retry_at=None,
                message='执行分工已切换：API取数和调控，智能体仅新建追投')
        elif data['phase'] in {'tasks','lookup','materials'} or not self.fresh(data.get('api_inventory') or {},run):
            if data.get('command') and self.a.clock()<parse_time(data['command']['expires_at']) and data['phase'] in {'prepare','dry_prepare'}:return False
            data.update(phase='api_inventory',command=None,yielding=False,retry_at=None,message='API正在核对在投任务和预算；网页不读取旧任务')
        else:return False
        self.h.save(db,run,data);return True

    @staticmethod
    def verify_retry_seconds(d,now,exc=None):
        """Fresh submissions recheck quickly; stale background readbacks back off."""
        retry_after=int(getattr(exc,'retry_after',0) or 0)
        cycle=int(d.get('verify_cycle',0) or 0)+1
        age=0.0
        for key in ('begin_at','started_at'):
            if d.get(key):
                try:age=max(0.0,(now-parse_time(d[key])).total_seconds());break
                except (TypeError,ValueError):pass
        if d.get('isolated_readback') or age>=600:
            if age>=21600:base=3600
            elif age>=3600:base=900
            else:base=300
        elif cycle<=1:base=20
        elif cycle<=3:base=30
        else:base=60
        return max(base,retry_after)

    def schedule(self):
        if self.h.api_scan_thread and self.h.api_scan_thread.is_alive():return
        with self.a.db() as db:
            run=db.execute("SELECT id,data FROM hybrid_runs WHERE state NOT IN ('completed','cancelled') AND json_extract(data,'$.phase') IN ('api_materials','api_inventory','verify') AND (json_extract(data,'$.retry_at') IS NULL OR json_extract(data,'$.retry_at')<=?) ORDER BY CASE WHEN json_extract(data,'$.phase')='verify' AND NOT COALESCE(json_extract(data,'$.isolated_readback'),0) THEN 0 WHEN json_extract(data,'$.phase') IN ('api_materials','api_inventory') THEN 1 ELSE 2 END,updated_at,id LIMIT 1",(self.h.now(),)).fetchone()
        if run:
            self.h.api_scan_thread=threading.Thread(target=self.scan,args=(run['id'],),name='hybrid-api-reader',daemon=True);self.h.api_scan_thread.start()

    def scan(self,rid):
        with self.a.db() as db:run=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone()
        if not run or run['state'] in {'completed','cancelled'}:return
        data=json.loads(run['data']);phase=data['phase']
        if phase=='api_materials':self.h.scan_api_materials(rid);return
        try:
            if phase=='verify':self.verify(run,data);return
            account,profile,_=self.h.scope(run,data)
            value=self.read_inventory(run,data,profile)
            with self.a.gate,self.h.lock,self.a.db() as db:
                current=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone()
                if not current or current['state'] in {'completed','cancelled'}:return
                d=json.loads(current['data'])
                if d['phase']!=phase:return
                self.h.scope(current,d);self.install(db,current,d,profile,value)
                self.h.save(db,current,d,'completed' if d.get('finished') else 'waiting')
        except Exception as exc:
            with self.a.gate,self.h.lock,self.a.db() as db:
                current=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(rid,)).fetchone()
                if not current or current['state'] in {'completed','cancelled'}:return
                d=json.loads(current['data'])
                if d['phase']!=phase:return
                if phase=='verify':
                    seconds=self.verify_retry_seconds(d,self.a.clock(),exc)
                    d['verify_cycle']=int(d.get('verify_cycle',0) or 0)+1
                    wait_label=f'{seconds//60}分钟后' if seconds>=120 else f'{seconds}秒后'
                    msg='原追投API核验等待：'+str(exc)[:200]+f'；{wait_label}再核验，不转网页读取或重复提交'
                else:
                    seconds=max(30,int(getattr(exc,'retry_after',60) or 60))
                    msg='API任务读取等待：'+str(exc)[:220]+'；不转网页读取或重复提交'
                d.update(command=None,retry_at=(self.a.clock()+timedelta(seconds=seconds)).isoformat(),message=msg)
                self.h.save(db,current,d)

    def verify(self,run,data):
        with self.a.db() as db:row=db.execute('SELECT * FROM api_actions WHERE id=?',(data.get('action_id'),)).fetchone()
        if not row:raise ValueError('原追投单不存在')
        if (data.get('permit') or {}).get('consumed') is not True and not data.get('readback_only'):
            raise ValueError('等待原一次提交阶段结束，不提前核验或重发')
        intent=json.loads(row['intent']);account=self.a.account(run['account_id']);api=self.a.api;at=self.a.clock()
        day=parse_time(row['created_at']).date().isoformat();name=intent['browser_batch']['task_name']
        with api.read_lock, (nullcontext() if data.get('isolated_readback') else prioritize_readback()):
            with api.lock:token=api._token(api.grant_for_account(account['id']))
            raw=pages(api,'control_tasks',{'advertiser_id':int(account['id']),'ad_id':int(run['plan_id']),
                'marketing_goal':'LIVE_PROM_GOODS','scene':'MATERIAL_ADD_BUDGET','filtering':{'search_keyword':name},
                'fields':list(TASK_FIELDS),'start_time':day+' 00:00:00','end_time':at.strftime('%Y-%m-%d %H:%M:%S')},'task_list','total_number',token)
        matches=[t for t in raw if t.get('name')==name]
        if len(matches)!=1:raise ValueError('API尚未返回唯一原任务，保留预算和素材占用')
        task=matches[0]
        for key,expected in [('advertiser_id',account['id']),('ad_id',run['plan_id'])]:
            if task.get(key) is not None and exact_id(task[key])!=expected:raise ValueError('API原任务归属不符')
        expected=intent['browser_batch']['after'];kind=expected['chase_type']
        if task.get('scene') not in (None,'MATERIAL_ADD_BUDGET'):raise ValueError('API原任务工具不符')
        mids=[exact_id(m.get('material_id')) for m in (task.get('material_list') or [])];materials_source='official_api'
        if not mids:
            original=data.get('submission_materials') or {}
            checked=parse_time(original.get('checked_at')) if original.get('checked_at') else None
            begin=parse_time(data['begin_at'])
            if original.get('source')!='validated_native_form' or original.get('material_ids')!=intent['material_ids'] or checked is None or not begin-timedelta(seconds=90)<=checked<=begin:
                raise ValueError('API未返回素材ID，且原单缺少创建前表单核对证据；保留原单，不重复创建')
            mids=list(original['material_ids']);materials_source='pre_submit_native_form'
        if len(set(mids))!=len(mids) or set(mids)!=set(intent['material_ids']):raise ValueError('API原任务素材集合不一致')
        budget=expected['daily_budget'] if kind=='控成本追投' else expected['total_budget']
        if amount(task.get('budget'))!=amount(budget) or task.get('smart_bid_type')!=intent['body']['smart_bid_type']:raise ValueError('API原任务预算或追投方式不一致')
        if kind=='放量追投':
            if amount(task.get('duration'))!=amount(expected['control_duration']):raise ValueError('API原任务时长未回读一致')
        else:
            contract=intent.get('api_readback_contract')
            if not contract:
                basis='净成交ROI' if expected.get('optimization_target') in {'综合ROI','综合营销ROI'} and intent.get('delivery_entry')=='chengfang' else None
                contract=chase_contract(expected,basis,delivery_entry=intent.get('delivery_entry'),for_browser_readback=True)['body']
            if task.get('external_action_name')!='直播间支付':raise ValueError('API原任务优化目标不一致')
            goal={'AD_CONVERT_TYPE_LIVE_PAY_ROI':'支付ROI','AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI':'净成交ROI'}.get(contract.get('deep_external_action'))
            if task.get('deep_external_action_name')!=goal:raise ValueError('API原任务ROI口径不一致')
            field='roi2_goal' if goal else 'bid'
            if amount(task.get(field))!=amount(contract[field]):raise ValueError('API原任务出价或ROI目标不一致')
        status=task.get('task_status')
        if status not in {'PROCESSING','DISABLE','FINISH','MANUAL_STOP','MANUAL_FINISH','PAUSE','STOP','COMPLETED','FROZEN','DELETED','LIVE_ROOM_OFF','OFFLINE_BALANCE','OFFLINE_BUDGET','OFFLINE_TIME','ROI2_DISABLE'}:raise ValueError('API原任务状态尚未明确')
        audience_checks={}
        if expected.get('audience'):
            from .chase_audience import check_api_fields
            audience_checks=check_api_fields(task.get('audience'),expected['audience'])
        proof={'source':'official_api','account_id':account['id'],'plan_id':run['plan_id'],'captured_at':at.isoformat(),'materials_source':materials_source,'audience_api_checks':audience_checks,
            'rows':[{'id':exact_id(task.get('id')),'name':name,'material_ids':mids,'after':expected,
                     'status':{'PROCESSING':'调控中','DISABLE':'已暂停','FROZEN':'调控结束','DELETED':'已删除'}.get(status,status)}]}
        with self.a.gate,self.h.lock,self.a.db() as db:
            current=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(run['id'],)).fetchone()
            if not current or current['state'] in {'completed','cancelled'}:return
            d=json.loads(current['data'])
            if d.get('action_id')!=row['id'] or d['phase']!='verify':return
            self.h.verify_result(db,current,d,proof,api=True)
            self.h.save(db,current,d,'completed' if d.get('finished') else 'waiting')
