"""Concrete API intents: confirm once, claim before POST, independently read back.

Scans and prepared cards never grant authority. Exact per-card confirmation or
a separate, explicit scoped automatic grant is required. Uncertain POSTs are
not retried.
"""
import copy
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from datetime import date, timedelta
from decimal import Decimal
from .official_api import exact_id, OfficialBusinessError, OfficialWriteTransport
from .official_rate import prioritize_readback
from .official_workflow import parent, fresh_parent, cached_parent, active_tasks, material_identity, material_query, digest, TASK_FIELDS
from .official_monitoring import pages
from .schema import now_cn, parse_time
from .actuation import amount
from .cost_guarantee import evaluate_cost_guarantee
from .official_contracts import chase_contract


class OfficialActions:
    def __init__(self,path,api,accounts,profile,*,clock=now_cn,gate=None,conflict_check=None,writer=None):
        from .execution_strategy import load
        self.execution_strategy=load(path)
        self.path=str(path);self.api=api;self.accounts=accounts;self.profile=profile;self.clock=clock
        self.lock=threading.RLock()
        self.gate=gate or threading.RLock();self.conflict_check=conflict_check or (lambda:None)
        self.writer=writer or OfficialWriteTransport(governor=getattr(api.transport,'governor',None))
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS api_actions(id TEXT PRIMARY KEY,state TEXT,intent TEXT,digest TEXT,created_at TEXT,expires_at TEXT,updated_at TEXT,result TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS api_followups(id TEXT PRIMARY KEY,action_id TEXT,account_id TEXT,plan_id TEXT,task_id TEXT,due_at TEXT,state TEXT,result TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS api_task_holds(account_id TEXT,plan_id TEXT,task_id TEXT,action_id TEXT,created_at TEXT,updated_at TEXT,active INTEGER,PRIMARY KEY(account_id,plan_id,task_id))')
            db.execute('CREATE TABLE IF NOT EXISTS api_material_exclusions(account_id TEXT PRIMARY KEY,material_ids TEXT,updated_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS api_material_failures(account_id TEXT,plan_id TEXT,batch_key TEXT,material_ids TEXT,failures INTEGER,last_at TEXT,until_at TEXT,reason TEXT,PRIMARY KEY(account_id,plan_id,batch_key))')
            db.execute("UPDATE api_followups SET state='unknown' WHERE state='submitting'")
            db.execute("UPDATE api_actions SET state='unknown' WHERE state='submitting'")
            db.execute("UPDATE api_actions SET state='draft',result=? WHERE state='checking'",(json.dumps({'phase':'preflight','message':'核验期间服务重启，尚未提交广告；请重新核对确认。','ad_changes':0},ensure_ascii=False),))
        from .official_schedules import APISchedules
        self.schedules=APISchedules(self)
        from .official_auto import AutoChase
        self.auto=AutoChase(self)
        from .official_rule_auto import AutoRules
        self.rule_auto=AutoRules(self)
        from .budget_recovery import BudgetRecovery
        self.budget_recovery=BudgetRecovery(self)
        self.api.excluded_materials_provider=self.material_exclusions
        self.api.recent_chase_materials_provider=lambda aid,pid:self.recent_chase_materials(aid,pid,include_pending=False)
        self.api.material_deferrals_provider=self.deferred_materials

    def deferrals(self,aid=None):
        with self.db() as db:rows=db.execute('SELECT * FROM api_material_failures WHERE until_at>? ORDER BY until_at',(self.clock().isoformat(),)).fetchall()
        return [{**dict(r),'material_ids':json.loads(r['material_ids'])} for r in rows if aid is None or r['account_id']==aid]

    def deferred_materials(self,aid,pid):
        return {mid for r in self.deferrals(aid) if r['plan_id']==pid for mid in r['material_ids']}

    EXPLICIT_MATERIAL_REJECTION=re.compile(r'(?:审核不通过|审核拒绝|审核失败|本批素材已删除)')

    def note_material_failure(self,aid,pid,mids,reason,*,db=None):
        mids=sorted({exact_id(mid) for mid in mids});key=digest(mids)
        explicit=bool(self.EXPLICIT_MATERIAL_REJECTION.search(str(reason)))
        with (nullcontext(db) if db is not None else self.db()) as conn:
            old=conn.execute('SELECT * FROM api_material_failures WHERE account_id=? AND plan_id=? AND batch_key=?',(aid,pid,key)).fetchone()
            recent=old and 0<=(self.clock()-parse_time(old['last_at'])).total_seconds()<1800
            expired=old and old['until_at'] and parse_time(old['until_at'])<=self.clock()
            failures=old['failures']+1 if recent and not expired else 1
            if explicit:
                target=self.clock()+timedelta(hours=12)
                prior=parse_time(old['until_at']) if old and old['until_at'] else None
                until=(max(prior,target) if prior and prior>self.clock() else target).isoformat()
            else:
                until=(old['until_at'] if old and old['until_at'] and parse_time(old['until_at'])>self.clock() else (self.clock()+timedelta(minutes=15)).isoformat()) if failures>=2 else None
            conn.execute('INSERT OR REPLACE INTO api_material_failures VALUES(?,?,?,?,?,?,?,?)',(aid,pid,key,json.dumps(mids),failures,self.clock().isoformat(),until,str(reason)[:300]))
        return {'failures':failures,'until_at':until,'material_ids':mids,'explicit_rejection':explicit}

    def clear_deferrals(self,p):
        if not isinstance(p,dict) or set(p)!={'account_id'}:raise ValueError('请指定本户')
        aid=self.account(p['account_id'])['id']
        with self.db() as db:db.execute('UPDATE api_material_failures SET failures=0,until_at=NULL WHERE account_id=?',(aid,))
        self.auto.audit(aid,'material_deferrals_cleared',{'message':'已清除本户临时暂缓；永久排除、当前在投核验和自动授权保持不变。','ad_changes':0})
        return {'ok':True,'ad_changes':0}

    def known_task_budget(self,aid,pid,tid):
        """A reservation, not a claim that the native task stopped or verified."""
        values=[]
        with self.db() as db:
            rows=db.execute("SELECT * FROM api_actions WHERE json_extract(intent,'$.account_id')=? AND json_extract(intent,'$.plan_id')=?",(aid,pid)).fetchall()
        for row in rows:
            i=json.loads(row['intent']);r=json.loads(row['result']) if row['result'] else {}
            if i['action']=='material_chase' and str(r.get('task_id') or r.get('reply',{}).get('id'))==tid and row['state'] not in {'cancelled','rejected','expired'}:
                values.append(amount(i['body']['budget']))
            if i.get('task_id')==tid and i['action']=='task_budget':
                values.append(amount(i['before']))
                if row['state'] in {'verified','submitting','unknown','submitted_pending_verification'}:values.append(amount(i['after']))
        if not values:raise ValueError('原任务缺少可保留的明确预算，暂缓新追投；不按零预算处理')
        return max(values)

    def recent_chase_materials(self,aid,pid,*,exclude=None,include_pending=True,db=None):
        """Reserve unfinished writes only; verified history is not a cooldown.

        Current processing status is checked by the fresh API/native task list.
        Unknown submissions remain reserved until their own receipt is resolved.
        """
        ids=set()
        with (nullcontext(db) if db is not None else self.db()) as conn:
            for r in conn.execute("SELECT * FROM api_actions WHERE json_extract(intent,'$.account_id')=? AND json_extract(intent,'$.plan_id')=? AND json_extract(intent,'$.action')='material_chase'",(aid,pid)):
                if r['id']==exclude:continue
                state=r['state']
                if state in {'draft','checking'} and self.clock()>=parse_time(r['expires_at']):continue
                if not include_pending or state not in {'draft','checking','submitting','unknown','submitted_pending_verification'}:continue
                ids.update(json.loads(r['intent']).get('material_ids',[]))
        return ids

    def material_exclusions(self,aid):
        with self.db() as db:r=db.execute('SELECT material_ids FROM api_material_exclusions WHERE account_id=?',(aid,)).fetchone()
        return json.loads(r[0]) if r else []

    def exclusion_status(self):
        with self.db() as db:
            return [{**dict(r),'material_ids':json.loads(r['material_ids'])} for r in db.execute('SELECT * FROM api_material_exclusions')]

    def require_allowed_materials(self,aid,ids):
        excluded=set(self.material_exclusions(aid)).intersection(ids)
        if excluded:raise ValueError('本批包含已排除追投的素材ID：'+'、'.join(sorted(excluded))+'；未提交')
        with self.db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='hybrid_runs'").fetchone():
                for row in db.execute('SELECT DISTINCT plan_id FROM hybrid_runs WHERE account_id=?',(aid,)):
                    if set(ids)&self.isolated_materials(aid,row['plan_id'],db=db):raise ValueError('本批包含后台核验中原单的素材，保留占用，不重复追投')

    def isolated_materials(self,aid,pid,*,db=None):
        from .chase_recovery import reservations
        with (nullcontext(db) if db is not None else self.db()) as conn:
            return {mid for r in reservations(conn,aid,pid) for mid in r['material_ids']}

    def isolated_budget(self,aid,pid,*,db=None):
        from .chase_recovery import reserved_total
        with (nullcontext(db) if db is not None else self.db()) as conn:return reserved_total(conn,aid,pid)

    def save_material_exclusions(self,p):
        if not isinstance(p,dict) or set(p)!={'account_id','material_ids'}:raise ValueError('请指定账户及排除素材ID列表')
        aid=self.account(p['account_id'])['id'];ids=p['material_ids']
        if not isinstance(ids,list) or len(ids)>10000 or any(not isinstance(mid,str) for mid in ids):raise ValueError('素材ID须以文本填写，最多10000条')
        ids=list(dict.fromkeys(exact_id(mid.strip()) for mid in ids))
        with self.local_claim_gate():
            with self.db() as db:
                db.execute('INSERT OR REPLACE INTO api_material_exclusions VALUES(?,?,?)',(aid,json.dumps(ids),self.clock().isoformat()))
                for row in db.execute("SELECT id,intent FROM api_actions WHERE state IN ('draft','checking') AND json_extract(intent,'$.account_id')=? AND json_extract(intent,'$.action')='material_chase'",(aid,)).fetchall():
                    if set(ids).intersection(json.loads(row['intent']).get('material_ids',[])):
                        db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=?",(self.clock().isoformat(),json.dumps({'message':'本批包含新加入排除列表的素材，未提交批次已取消，请按新列表重新筛选。','ad_changes':0},ensure_ascii=False),row['id']))
            # The final claim checks the live exclusion set too, including a
            # scan that was already running when this local setting changed.
            with self.api.lock:
                for key in list(self.api.workflows):
                    if key.startswith(aid+':'):self.api.workflows.pop(key,None)
            if hasattr(self,'hybrid'):self.hybrid.exclusions_changed(aid)
        self.auto.audit(aid,'material_exclusions_saved',{'message':f'已保存本户排除追投素材{len(ids)}条；API与网页共用，已有投放任务保持原状态。','material_ids':ids,'ad_changes':0})
        return {'ok':True,'account_id':aid,'material_ids':ids,'ad_changes':0}

    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    @contextmanager
    def local_claim_gate(self):
        if not self.gate.acquire(blocking=False):raise ValueError('其他投放动作正在完成状态核验，本次未创建或确认新单；请稍后重试。')
        try:yield
        finally:self.gate.release()

    def status(self, *, pending_only=False, console=False):
        with self.db() as db:
            db.execute("UPDATE api_actions SET state='expired' WHERE state='draft' AND expires_at<?",(self.clock().isoformat(),))
            if pending_only:
                query="SELECT * FROM api_actions WHERE state IN ('draft','checking','submitting','unknown','submitted_pending_verification') ORDER BY created_at DESC"
            else:
                query="SELECT * FROM api_actions WHERE id IN (SELECT id FROM api_actions ORDER BY created_at DESC LIMIT 200) OR state IN ('draft','checking','submitting','unknown','submitted_pending_verification') ORDER BY created_at DESC"
            records = [self.record(row) for row in db.execute(query)]
        if console:
            for record in records:
                intent = record['intent']
                if isinstance(intent.get('browser_batch'), dict):
                    batch = intent['browser_batch']
                    intent['browser_batch'] = {key: batch[key] for key in ('material_ids','after','task_name') if key in batch}
                if isinstance(intent.get('cost_guarantee'), dict):
                    intent['cost_guarantee'] = {'warnings': intent['cost_guarantee'].get('warnings', [])}
                intent.pop('api_readback_contract', None)
        return records

    def has_pending(self):
        with self.db() as db:
            return (db.execute("SELECT 1 FROM api_actions WHERE state IN ('draft','checking','submitting','unknown','submitted_pending_verification') LIMIT 1").fetchone() is not None
                or db.execute("SELECT 1 FROM api_followups WHERE state IN ('submitting','unknown') LIMIT 1").fetchone() is not None)

    def activity_history(self, parameters):
        """Read account-local action history without fetching or changing ads."""
        allowed={'account_id','page','page_size','action','state','day','keyword'}
        if set(parameters)-allowed or 'account_id' not in parameters:
            raise ValueError('调控记录查询参数无效')
        aid=exact_id(parameters['account_id'])
        if not any(account['id']==aid for account in self.accounts()):
            raise ValueError('账户未登记')
        page_text=str(parameters.get('page','1'))
        size_text=str(parameters.get('page_size','20'))
        if not page_text.isascii() or not page_text.isdecimal() or not 1<=int(page_text)<=1000000:
            raise ValueError('页码无效')
        if size_text not in {'20','50','100'}:raise ValueError('每页条数应为20、50或100')
        page=int(page_text);size=int(size_text)
        categories={
            'budget':['task_budget','plan_budget'], 'pause':['pause_task','pause_plan','resume_task'],
            'duration':['task_duration'], 'roi':['plan_roi'],
            'chase':['material_chase'], 'boost':['boost'], 'slowdown':['slowdown_schedule'],
        }
        states={
            'verified':['verified'],
            'pending':['draft','checking','submitting','unknown','submitted_pending_verification','scheduled'],
            'acknowledged':['active_acknowledged','completed_acknowledged'],
            'failed':['rejected','cancelled','expired','blocked','missed'],
        }
        action=parameters.get('action','all');state=parameters.get('state','all')
        if action!='all' and action not in categories:raise ValueError('操作类型无效')
        if state!='all' and state not in states:raise ValueError('执行结果无效')
        conditions=["json_extract(intent,'$.account_id')=?"];values=[aid]
        if action!='all':
            items=categories[action]
            conditions.append("json_extract(intent,'$.action') IN ("+','.join('?' for _ in items)+')')
            values.extend(items)
        if state!='all':
            items=states[state]
            conditions.append('state IN ('+','.join('?' for _ in items)+')');values.extend(items)
        day=parameters.get('day','')
        if day:
            if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:raise ValueError('日期无效')
            conditions.append('substr(created_at,1,10)=?');values.append(day)
        keyword=parameters.get('keyword','')
        if not isinstance(keyword,str) or len(keyword)>100:raise ValueError('搜索内容最多100字')
        if keyword.strip():
            literal=keyword.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            fields=['id',"json_extract(intent,'$.plan_id')","json_extract(intent,'$.task_id')",
                "json_extract(intent,'$.plan_name')","json_extract(intent,'$.material_ids')",
                "json_extract(result,'$.task_id')"]
            conditions.append('('+' OR '.join(field+" LIKE ? ESCAPE '\\'" for field in fields)+')')
            values.extend(['%'+literal+'%']*len(fields))
        where=' AND '.join(conditions)
        with self.db() as db:
            total=db.execute('SELECT COUNT(*) FROM api_actions WHERE '+where,values).fetchone()[0]
            pages=max(1,(total+size-1)//size);page=min(page,pages)
            rows=db.execute('SELECT * FROM api_actions WHERE '+where+' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                [*values,size,(page-1)*size]).fetchall()
            records=[self.record(row) for row in rows]
            if records:
                by_id={record['id']:record for record in records}
                placeholders=','.join('?' for _ in by_id)
                for row in db.execute('SELECT * FROM api_followups WHERE account_id=? AND action_id IN ('+placeholders+') ORDER BY due_at,id',[aid,*by_id]):
                    followup=dict(row)
                    followup['result']=json.loads(row['result']) if row['result'] else None
                    by_id[row['action_id']].setdefault('followups',[]).append(followup)
        return {'ok':True,'account_id':aid,'records':records,'total':total,'page':page,'page_size':size,'pages':pages}

    @staticmethod
    def record(row):
        intent=json.loads(row['intent'])
        result=json.loads(row['result']) if row['result'] else None
        timed_task=(row['state']=='unknown' and intent['action'] in {'pause_task','task_budget'} and result
            and result.get('native_code')==51010 and result.get('post_sent') is True
            and result.get('submission_uncertain') is True)
        return {'backend':'official_api','id':row['id'],'state':row['state'],'intent':intent,
            'intent_digest':row['digest'],'created_at':row['created_at'],'expires_at':row['expires_at'],
            'updated_at':row['updated_at'],'result':result,
            'readback_available':(intent.get('execution_channel')!='browser' and (row['state']=='submitted_pending_verification' or bool(timed_task) or row['state']=='unknown' and bool(intent.get('recovery_authority')))) or (intent.get('operation')=='browser_rule' and row['state'] in {'unknown','submitted_pending_verification'}),
            'failure_detected':bool(row['state']=='submitted_pending_verification' and OfficialActions.rejected_reply(intent,(result or {}).get('reply'))),
            'hold_available':(intent.get('execution_channel')!='browser' or intent.get('operation')=='browser_rule') and intent.get('task_id') is not None and intent['action'] in {'pause_task','task_budget','task_duration'} and row['state'] in {'draft','unknown','submitted_pending_verification','rejected'}}

    @staticmethod
    def rejected_reply(intent,reply):
        """An exact, explicit native item failure is not an uncertain write."""
        if intent.get('action')!='task_budget' or not isinstance(reply,dict):return None
        values=reply.get('results')
        if not isinstance(values,list) or len(values)!=1 or not isinstance(values[0],dict):return None
        item=values[0]
        try:matched=exact_id(item.get('task_id'))==intent.get('task_id')
        except ValueError:return None
        if not matched or item.get('status') is not False:return None
        reason=str(item.get('error_message') or '平台明确返回该任务预算修改失败')[:500]
        temporary=any(word in reason for word in ('FAULT-2001','系统开小差','系统繁忙','稍后重试'))
        return {'phase':'submission_reply','message':'平台明确返回本笔预算修改失败：'+reason+'。原单已结束，后续按最新规则重新判断。',
            'task_id':intent['task_id'],'native_rejected':True,'submission_uncertain':False,'post_sent':True,
            'retryable':temporary,'ad_changes':0,'reply':copy.deepcopy(reply)}

    def holds(self,aid=None,pid=None):
        with self.db() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM api_task_holds WHERE active=1 ORDER BY updated_at DESC')]
        return [r for r in rows if (aid is None or r['account_id']==aid) and (pid is None or r['plan_id']==pid)]

    def held(self,intent):
        return bool(intent.get('task_id') and any(r['task_id']==intent['task_id'] for r in self.holds(intent['account_id'],intent['plan_id'])))

    def hold_task(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'id','account_id'}:raise ValueError('请指定本户原单')
        account=self.account(payload['account_id'])
        with self.local_claim_gate(),self.db() as db:
            row=db.execute('SELECT * FROM api_actions WHERE id=?',(payload['id'],)).fetchone()
            if not row:raise ValueError('原单不存在')
            record=self.record(row);intent=record['intent']
            if intent['account_id']!=account['id'] or not record['hold_available']:raise ValueError('本单当前不能跳过；正在核验的单可取消，提交中的单须等待回执')
            now=self.clock().isoformat()
            db.execute('INSERT INTO api_task_holds VALUES(?,?,?,?,?,?,1) ON CONFLICT(account_id,plan_id,task_id) DO UPDATE SET action_id=excluded.action_id,updated_at=excluded.updated_at,active=1',
                (account['id'],intent['plan_id'],intent['task_id'],row['id'],now,now))
            if row['state']=='draft':db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=? AND state='draft'",(now,row['id']))
        self.auto.audit(account['id'],'api_task_held',{'action_id':row['id'],'plan_id':intent['plan_id'],'task_id':intent['task_id'],'message':'已人工跳过该任务，其他任务继续；原提交记录及未核实预算占用保留。','ad_changes':0})
        self.wake(account['id'])
        return {'ok':True,'ad_changes':0}

    def resume_task(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'account_id','plan_id','task_id'}:raise ValueError('请指定本户母计划及任务')
        account=self.account(payload['account_id']);pid=exact_id(payload['plan_id']);tid=exact_id(payload['task_id'])
        with self.local_claim_gate(),self.db() as db:
            db.execute('UPDATE api_task_holds SET active=0,updated_at=? WHERE account_id=? AND plan_id=? AND task_id=?',(self.clock().isoformat(),account['id'],pid,tid))
        self.auto.audit(account['id'],'api_task_resumed',{'plan_id':pid,'task_id':tid,'message':'已恢复该任务，先核验原单及最新规则，不重发旧请求。','ad_changes':0})
        self.wake(account['id']);return {'ok':True,'ad_changes':0}

    def wake(self,aid):
        with self.db() as db:
            for table in ('api_auto','api_rule_auto'):
                db.execute(f'UPDATE {table} SET next_at=? WHERE account_id=? AND enabled=1',(self.clock().isoformat(),aid))

    def resolve_rejected(self,rid):
        """Close a stored explicit failure locally, even after grant renewal."""
        with self.local_claim_gate(),self.db() as db:
            row=db.execute('SELECT * FROM api_actions WHERE id=?',(rid,)).fetchone()
            if not row or row['state'] not in {'unknown','submitted_pending_verification'}:return None
            i=json.loads(row['intent']);result=json.loads(row['result'] or '{}')
            rejection=self.rejected_reply(i,result.get('reply'))
            if not rejection:return None
            rejection['resolved_at']=self.clock().isoformat()
            db.execute("UPDATE api_actions SET state='rejected',updated_at=?,result=? WHERE id=? AND state=?",(self.clock().isoformat(),json.dumps(rejection,ensure_ascii=False),rid,row['state']))
            record=self.record(db.execute('SELECT * FROM api_actions WHERE id=?',(rid,)).fetchone())
        self.auto.audit(i['account_id'],'api_action_resolved',{'action_id':rid,'task_id':i.get('task_id'),'message':rejection['message'],'ad_changes':0})
        self.wake(i['account_id']);return record

    def budget_tasks_with_holds(self,aid,pid,tasks):
        """Reserve the larger possible budget until an explicitly held edit settles."""
        held={r['task_id'] for r in self.holds(aid,pid)}
        if not held:return tasks
        values={str(t['id']):copy.deepcopy(t) for t in tasks}
        if len(values)!=len(tasks):raise ValueError('任务ID重复，不能计算跳过任务的预算占用')
        with self.db() as db:
            for row in db.execute("SELECT intent FROM api_actions WHERE state IN ('submitting','unknown','submitted_pending_verification') AND json_extract(intent,'$.account_id')=? AND json_extract(intent,'$.plan_id')=?",(aid,pid)):
                i=json.loads(row['intent']);tid=i.get('task_id')
                if tid not in held:continue
                from .chase_recovery import held_native_budget
                reserve=held_native_budget(i)
                if reserve is None:continue
                if tid in values:
                    values[tid]['budget']=str(max(amount(values[tid]['budget']),reserve));values[tid]['status']='PROCESSING'
                else:values[tid]={'id':tid,'scene':i['task_scene'],'budget':str(reserve)}
        return list(values.values())

    def account(self,aid):
        aid=exact_id(aid)
        rows=[a for a in self.accounts() if a['id']==aid and a.get('observe',True)]
        if len(rows)!=1:raise ValueError('账户不在当前已启用的API监测范围')
        return rows[0]

    @staticmethod
    def semantic(intent):
        kind=intent['action']
        action={'material_chase':'create_control','pause_task':'control_pause','resume_task':'control_resume','pause_plan':'pause_plan','task_budget':'control_budget_up','task_duration':'control_duration_up'}.get(kind)
        if kind in {'plan_budget','plan_roi'}:action=('budget_' if kind=='plan_budget' else 'roi_target_')+('up' if amount(intent['after'])>amount(intent['before']) else 'down')
        return action

    def _history(self):
        with self.db() as db:
            rows=[self.record(r) for r in db.execute('SELECT * FROM api_actions')]
        return [{**r['intent'],'action':self.semantic(r['intent']),'entity_id':r['intent'].get('task_id',r['intent']['plan_id']),
            'state':r['state'],'at':r['updated_at']} for r in rows]

    def history(self,aid):
        rows=[r for r in self._history() if r['account_id']==aid]
        grant=self.auto.row(aid)
        # This is rule-evaluation history, not the audit ledger. Unlimited F5
        # creations must not exhaust the daily allowance for F1-F4/F6. Their
        # complete records and unresolved-write reservations remain intact.
        if grant and grant.get('batches_unlimited'):
            rows=[r for r in rows if not (r['action']=='create_control' and r.get('material_ids')
                  and r.get('auto_authority') and r['plan_id']==grant['plan_id'])]
        return rows

    def followups(self):
        with self.db() as db:return [dict(r)|{'result':json.loads(r['result']) if r['result'] else None} for r in db.execute('SELECT * FROM api_followups')]

    def refresh_automatic_profile(self,aid):
        """Explicit Save-and-update from the UI; never enables an inactive grant.

        Local DB only: the caller holds the claim gate. Rotate authority IDs so
        any in-flight old-profile preflight stops at the last submission gate.
        """
        self.account(aid);profile,rev=self.profile(aid);updated=[]
        with self.db() as db:
            for table in ('api_auto','api_rule_auto'):
                cursor=db.execute(f"UPDATE {table} SET profile_digest=?,profile_revision=?,grant_id=?,next_at=?,state='enabled',message='用户已保存并更新本户自动规则，按新参数重新检查。' WHERE account_id=? AND enabled=1",
                    (digest(profile),rev,secrets.token_hex(16),self.clock().isoformat(),aid))
                if cursor.rowcount:updated.append(table)
            for row in db.execute("SELECT * FROM api_actions WHERE state='draft'").fetchall():
                intent=json.loads(row['intent'])
                if intent['account_id']==aid and (intent.get('auto_authority') or intent.get('rule_auto_authority')):
                    db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=?",(self.clock().isoformat(),row['id']))
        self.auto.audit(aid,'automatic_rules_updated',{'channels':updated,'profile_revision':rev,'ad_changes':0})
        return updated

    def prepare(self,payload,*,rule_check=None,auto_authority=None,rule_auto_authority=None,recovery_authority=None):
        if not isinstance(payload,dict) or set(payload)-{'account_id','plan_id','action','batch_key','task_id','reason','value','off_at','duration_minutes','duration'}:
            raise ValueError('API调控单字段不匹配')
        account=self.account(payload.get('account_id'));pid=exact_id(payload.get('plan_id'));action=payload.get('action')
        local_schedule=action=='slowdown_schedule'
        if payload.get('task_id') and self.held({'account_id':account['id'],'plan_id':pid,'task_id':str(payload['task_id'])}):raise ValueError('该任务已人工跳过，请先在卡单处理区恢复')
        p=cached_parent(self.api,account,pid) if local_schedule else fresh_parent(self.api,account,pid)
        now=self.clock();profile,revision=self.profile(account['id'])
        intent={'account_id':account['id'],'account_name':account.get('name',account['id']),'plan_id':pid,
            'plan_name':p['name'],'delivery_entry':p['delivery_entry'],'scene':account['scene'],
            'action':action,'before':None,'after':None,'reason':str(payload.get('reason','按本户已保存规则准备'))[:500],
            'profile_digest':digest(profile),'profile_revision':revision,'parent_budget':p['budget'],
            'parent_bid_type':p['smart_bid_type'],'parent_goal':p['deep_external_action'],
            'parent_target_roi':p.get('target_roi'),
            'parent_marketing_services_mode':p.get('marketing_services_mode'),
            'configuration_source':p['configuration_source'],'notes':[]}
        if rule_check:intent['rule_check']=copy.deepcopy(rule_check)
        if auto_authority:intent['auto_authority']=copy.deepcopy(auto_authority)
        if rule_auto_authority:intent['rule_auto_authority']=copy.deepcopy(rule_auto_authority)
        if recovery_authority:
            intent['recovery_authority']=copy.deepcopy(recovery_authority)
            payload={**payload,'task_id':exact_id(payload.get('task_id'))}
            self.budget_recovery.build_intent(intent,payload,p,account)
        elif action=='material_chase':
            intent.update(target_kind='control_task',parent_plan_id=pid)
            workflow=self.api.workflows.get(account['id']+':'+pid,{})
            # A long automatic sweep may take more than ten minutes. Its
            # original batch stays immutable; confirm still re-reads every
            # selected ID and every rule condition before any business POST.
            if not workflow or not auto_authority and not 0<=(now-parse_time(workflow.get('materials_captured_at',workflow['captured_at']))).total_seconds()<=600:raise ValueError('素材扫描已过期，请重新扫描后准备')
            matches=[b for b in workflow.get('batches',[]) if b['key']==payload.get('batch_key')]
            if len(matches)!=1 or not matches[0]['ready']:raise ValueError('本批次未通过规则及API模式检查')
            batch=copy.deepcopy(matches[0])
            if batch['profile_digest']!=intent['profile_digest']:raise ValueError('本户规则已变化，请重新扫描')
            if account['scene']!='live' or p['smart_bid_type']!='SMART_BID_CUSTOM' or p['configuration_source']!='plan_list':raise ValueError('本批创建须有明确的直播控成本母计划')
            mids=batch['material_ids']
            self.require_allowed_materials(account['id'],mids)
            if not 1<=len(mids)<=20 or len(set(mids))!=len(mids):raise ValueError('批次素材须为1至20条且不可重复')
            contract=batch.get('native_contract') or chase_contract(batch['rule'])
            budget=amount(contract['budget'])
            self._budget_guard(p,workflow['tasks'],budget,account['id'],pid)
            intent.update(material_ids=mids,materials=batch['materials'],batch=batch,
                after={'daily_budget':str(budget) if contract['budget_kind']=='daily' else None,'total_budget':str(budget) if contract['budget_kind']=='total' else None,
                    'bid':contract['body'].get('bid'),'roi2_goal':contract['body'].get('roi2_goal'),'optimization_target':contract['goal_label']},
                native_contract=contract,auto_stop_hours=contract['auto_stop_hours'],
                notes=['整批共用预算，不是每条素材各一份预算。']+contract['notes']+([] if contract['auto_stop_hours'] else ['本任务长期有效，直到暂停或结束；本单未自动附加结束排程。']))
            intent['body']={'advertiser_id':int(account['id']),'ad_id':int(pid),'scene':'MATERIAL_ADD_BUDGET',
                'material_ids':[int(exact_id(mid)) for mid in mids],'material_type':'VIDEO',
                **contract['body']}
            intent['operation']='create_control_task'
        elif action in {'pause_task','task_budget','task_duration'}:
            intent.update(target_kind='control_task',parent_plan_id=pid)
            tid=exact_id(payload.get('task_id'));workflow=self.api.workflows.get(account['id']+':'+pid,{})
            if not workflow or not 0<=(now-parse_time(workflow['captured_at'])).total_seconds()<=600:raise ValueError('当前任务观察已过期，请重新读取')
            rows=[t for t in workflow.get('tasks',[]) if t['id']==tid]
            if len(rows)!=1 or rows[0]['status']!='PROCESSING':raise ValueError('任务须明确处于调控中')
            intent.update(task_id=tid,task_scene=rows[0]['scene'],before='PROCESSING',after='暂停',operation='pause_control_task',
                body={'advertiser_id':int(account['id']),'task_ids':[int(tid)],'opt_type':'PAUSE'})
            if action!='pause_task':
                task=rows[0];value=amount(payload.get('value'));field='budget' if action=='task_budget' else 'duration'
                before=task[field]
                if before is None or value<=amount(before):raise ValueError('任务增加值须大于当前明确值')
                if action=='task_duration' and (not Decimal('1.5')<=value<=24 or value%Decimal('.5') or not .5<=before<=24):raise ValueError('延长后的原生总时长须为1.5至24小时且步进0.5；长期任务不能设置固定时长')
                if action=='task_budget':
                    if task['scene']=='MATERIAL_ADD_BUDGET':self._budget_guard(p,[t for t in workflow['tasks'] if t['id']!=tid],value,account['id'],pid)
                    elif value>amount(p['budget']):raise ValueError('任务预算不能超过母计划预算')
                intent.update(before=str(amount(before)),after=str(value),task_before={k:task[k] for k in ('budget','duration','smart_bid_type','roi2_goal','bid')},
                    operation='update_task_'+field,body={'advertiser_id':int(account['id']),'update_'+field+'_infos':[{'task_id':int(tid),field:float(value)}]})
        elif action=='pause_plan':
            if p['opt_status']!='ENABLE':raise ValueError('本计划未明确启用')
            intent.update(before='ENABLE',after='DISABLE',operation='pause_plan',
                body={'advertiser_id':int(account['id']),'ad_ids':[int(pid)],'opt_status':'DISABLE'})
        elif action in {'plan_budget','plan_roi'}:
            value=amount(payload.get('value'));field='budget' if action=='plan_budget' else 'target_roi'
            before=amount(p.get(field))
            if value==before:raise ValueError('设置值与当前值相同')
            change=(value-before)/before
            if action=='plan_budget' and (change>Decimal('.2') or change<Decimal('-.3')):raise ValueError('主计划预算单次上调不超过20%、下调不超过30%；请修改明确目标值')
            if action=='plan_roi' and abs(change)>Decimal('.2'):raise ValueError('主计划ROI单次调整幅度不得超过20%')
            if action=='plan_roi' and p['smart_bid_type']!='SMART_BID_CUSTOM':raise ValueError('仅控成本计划可以修改ROI目标')
            if action=='plan_roi':intent['notes'].append('本单只提交ROI数值，沿用当前原生优化目标；当前口径：'+{'net':'净成交ROI','overall_payment':'支付ROI'}.get(p.get('target_roi_basis'),'接口未明确返回，核对时须留意当前计划口径'))
            native={'ad_id':int(pid),'budget' if action=='plan_budget' else 'roi2_goal':float(value)}
            if action=='plan_roi' and p['deep_external_action'] is not None:native['deep_external_action']=p['deep_external_action']
            intent.update(before=str(before),after=str(value),operation='update_plan_budget' if action=='plan_budget' else 'update_plan_roi',
                body={'advertiser_id':int(account['id']),'update_budget_infos' if action=='plan_budget' else 'update_roi2_infos':[native]})
        elif action=='slowdown_schedule':
            intent.update(self.schedules.prepare(payload,account,p))
            intent['notes'].append('本地核对单使用已读取的计划配置（'+p['captured_at']+'）；确认仅登记本地排程，到点前重新读取官方配置并核验，条件变化不执行。')
        elif action=='boost':
            budget=amount(payload.get('value'));duration=amount(payload.get('duration'))
            if account['scene']!='live' or not 100<=budget<=300 or not 1<=duration<=24 or duration%Decimal('.5'):raise ValueError('一键起量首测预算须100至300元，时长1至24小时且步进0.5小时')
            if budget>amount(p['budget']):raise ValueError('起量预算不能超过母计划预算')
            intent.update(target_kind='control_task',parent_plan_id=pid,after={'budget':str(budget),'duration':str(duration)},operation='create_control_task',
                notes=['一键起量消耗不支持成本保障；本单优化目标为直播间购买。'],body={'advertiser_id':int(account['id']),'ad_id':int(pid),'scene':'SMART_BOOST',
                    'budget':float(budget),'duration':float(duration),'external_action':'AD_CONVERT_TYPE_LIVE_SUCCESSORDER_PAY'})
        else:raise ValueError('未知的API调控动作')
        guard=self._guard(intent,account)
        if not guard['allowed']:raise ValueError('；'.join(guard['blocking_reasons']))
        intent['cost_guarantee']=guard
        with (nullcontext() if local_schedule else self.lock),(self.local_claim_gate() if local_schedule else self.gate),self.db() as db:
            self.conflict_check()
            if local_schedule:
                for row in db.execute("SELECT * FROM api_actions WHERE state IN ('draft','scheduled')"):
                    prior=json.loads(row['intent'])
                    keys=('account_id','plan_id','action','after','parent_budget','parent_bid_type','parent_goal','parent_marketing_services_mode')
                    live=False
                    if row['state']=='scheduled' and prior.get('action')=='slowdown_schedule':live=parse_time(prior['after']['trigger_at'])>now
                    if all(prior.get(k)==intent.get(k) for k in keys) and (live or row['state']=='draft' and parse_time(row['expires_at'])>now):return self.record(row)
            self._pending(db,intent)
            rid=secrets.token_hex(12)
            if action in {'material_chase','boost'}:intent['body']['name']='巡航API_'+now.strftime('%m%d_%H%M')+'_'+rid[:6]
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,NULL)',(rid,'draft',json.dumps(intent,ensure_ascii=False),digest(intent),
                now.isoformat(),(now+timedelta(minutes=15)).isoformat(),now.isoformat()))
            return self.record(db.execute('SELECT * FROM api_actions WHERE id=?',(rid,)).fetchone())

    def _budget_guard(self,p,tasks,budget,aid,pid):
        tasks=self.budget_tasks_with_holds(aid,pid,tasks)
        current=[t for t in tasks if t['scene']=='MATERIAL_ADD_BUDGET']
        if any(t['budget'] is None for t in current):raise ValueError('已有追投任务预算缺失，不能核对母计划预算占比')
        total=sum((amount(t['budget']) for t in current),Decimal(0))+self.isolated_budget(aid,pid)
        if budget+total>amount(p['budget'])*Decimal('.3'):raise ValueError('当前追投与新批次预算合计超过母计划预算30%的用户策略')

    def _guard(self,intent,account):
        kind=intent['action'];action=self.semantic(intent) or ('create_control' if kind=='boost' else 'smart_control_create' if kind=='slowdown_schedule' else None)
        context={'account_id':account['id'],'plan_id':intent['plan_id'],'scene':account['scene'],
            'product_family':'chengfang' if intent['delivery_entry']=='chengfang' else 'all_domain',
            'tool':('material_chase_volume' if intent['body'].get('smart_bid_type')=='SMART_BID_CONSERVATIVE' else 'material_chase_cost') if kind=='material_chase' else 'one_click_boost' if kind=='boost' else 'one_click_slowdown' if kind=='slowdown_schedule' else 'main_plan' if kind in {'pause_plan','plan_budget','plan_roi'} else 'unknown',
            'delivery_mode':'cost_control' if intent['parent_bid_type']=='SMART_BID_CUSTOM' else 'volume',
            'history_known':False,'day_initial_budget_verified':False}
        context['saved_rule_budget_target_verified']=bool(getattr(self,'rule_auto',None) and self.rule_auto.saved_budget_target(intent))
        history=self._history();result=evaluate_cost_guarantee(context,{**intent,'action':action},history,now=self.clock())
        profile,_=self.profile(account['id'])
        daily=sum(h['state'] in {'verified','unknown','submitting','submitted_pending_verification','active_acknowledged','completed_acknowledged'} and parse_time(h['at']).date()==self.clock().date() for h in self.history(account['id']))
        unlimited_f5=False
        if kind=='material_chase':
            row=self.auto.row(account['id'])
            if row and row.get('batches_unlimited'):
                try:self.auto.verify_scope(intent);unlimited_f5=True
                except ValueError:pass
        if not unlimited_f5 and daily>=profile['safety']['max_actions_per_day']:
            result['allowed']=False;result['blocking_reasons'].append('本户已达到单日API调控数量上限')
        return result

    def _pending(self,db,intent,exclude=None,*,hybrid_rule_run=None):
        held={h['task_id'] for h in self.holds(intent['account_id'],intent['plan_id'])}
        if hasattr(self,'hybrid_rules'):
            for run in db.execute("SELECT id FROM hybrid_rule_runs WHERE account_id=? AND state NOT IN ('completed','cancelled')",(intent['account_id'],)):
                if run['id']!=hybrid_rule_run:raise ValueError('本户规则网页补位正在处理原单，API暂不并行提交')
        if intent.get('task_id') in held:raise ValueError('该任务已人工跳过，请在卡单处理区恢复后再调控')
        if hasattr(self,'hybrid') and self.hybrid.active(intent['account_id'],writes_only=True) and not (intent['action']=='auto_rule_scan' and self.hybrid.read_only_active(intent['account_id'])):
            raise ValueError('本户网页补位正在读取或核验，API暂不并行提交')
        if db.execute("SELECT 1 FROM api_followups WHERE account_id=? AND plan_id=? AND state IN ('submitting','unknown')",(intent['account_id'],intent['plan_id'])).fetchone():raise ValueError('本计划定时暂停结果未核实，已停止后续提交')
        for row in db.execute("SELECT * FROM api_schedules WHERE state IN ('scheduled','active_acknowledged','creating','stopping','unknown')"):
            if row['account_id']==intent['account_id'] and row['plan_id']==intent['plan_id'] and (intent['action']=='slowdown_schedule' or row['state'] in {'creating','stopping','unknown'}):raise ValueError('本计划已有控量排程或结果待核实，请先处理原排程')
        for row in db.execute("SELECT * FROM api_actions WHERE state IN ('draft','checking','submitting','unknown','submitted_pending_verification')"):
            if row['id']==exclude:continue
            prior=json.loads(row['intent'])
            if prior['account_id']==intent['account_id'] and prior['plan_id']==intent['plan_id']:
                from .chase_recovery import reservations,independent,native_task_independent
                isolated=next((r for r in reservations(db,intent['account_id'],intent['plan_id']) if r['action_id']==row['id']),None)
                if isolated and (independent(isolated,intent) or native_task_independent(db,isolated,intent,hybrid_rule_run,self.clock())):continue
                if row['state'] in {'unknown','submitted_pending_verification'} and prior.get('task_id') in held and intent['action'] in {'auto_rule_scan','pause_task','task_budget','task_duration','material_chase'}:continue
                raise ValueError(f"本母计划已有待确认、提交中或结果不明的API动作：原单 {row['id']}，任务 {prior.get('task_id',prior['plan_id'])}；请在“卡单处理”区核验、取消或跳过")
        if intent['action']=='material_chase':
            if not intent['batch']['allow_running_materials'] and set(intent['material_ids'])&self.recent_chase_materials(intent['account_id'],intent['plan_id'],exclude=exclude):
                raise ValueError('本批素材已有尚未完成的追投原单；核验结束前不重复提交')

    def _fresh(self,intent,account,token):
        if intent['action']=='material_chase':self.require_allowed_materials(account['id'],intent['material_ids'])
        profile,revision=self.profile(account['id'])
        if intent['action']!='slowdown_schedule' and (digest(profile)!=intent['profile_digest'] or revision!=intent['profile_revision']):raise ValueError('本户规则已变化，请重新准备')
        detail=self.api.transport('plan_detail',{'advertiser_id':int(account['id']),'ad_id':int(intent['plan_id'])},token)
        if exact_id(detail.get('ad_id'))!=intent['plan_id'] or detail.get('marketing_goal')!=('LIVE_PROM_GOODS' if account['scene']=='live' else 'VIDEO_PROM_GOODS'):
            raise ValueError('官方当前计划身份或场景不一致')
        if detail.get('advertiser_id') is not None and exact_id(detail['advertiser_id'])!=account['id']:raise ValueError('官方当前计划所属账户不一致')
        from .delivery_entry import ADLAB_SCENES
        if isinstance(detail.get('adlab_scene'),str) and detail['adlab_scene']!=ADLAB_SCENES[intent['delivery_entry']]:raise ValueError('官方当前计划入口已变化')
        known=parent(self.api,account,intent['plan_id'],require_fresh=False)
        needs_effects=intent.get('rule_check',{}).get('feature_id')=='f6'
        p=None;settings=detail.get('delivery_setting');family=detail.get('adlab_scene')
        # The current detail contract documents numeric family 0=all-domain,
        # 1=Chengfang: /labels/12/docs/1804362305657868. Exact detail already
        # contains the settings needed for task control; do not make every
        # pause depend on another whole-family list request.
        if type(family) is int and family in {0,1}:
            if family!={'uni_promotion':0,'chengfang':1}[intent['delivery_entry']]:raise ValueError('官方当前计划入口已变化')
            required={'smart_bid_type','budget','deep_external_action'}
            if intent['parent_bid_type']=='SMART_BID_CUSTOM':required.add('roi2_goal')
            mode=settings.get('marketing_services_mode',detail.get('marketing_services_mode')) if isinstance(settings,dict) else None
            if (isinstance(settings,dict) and 'marketing_services_mode' in settings or 'marketing_services_mode' in detail) and mode!=intent.get('parent_marketing_services_mode'):
                raise ValueError('母计划营销服务模式已变化，请重新准备')
            complete=(isinstance(settings,dict) and required<=settings.keys() and
                {'opt_status','status'}<=detail.keys() and mode==intent.get('parent_marketing_services_mode'))
            if complete and not needs_effects:
                from .official_monitoring import plan_record
                info={**settings,**{k:detail.get(k) for k in ('name','opt_status','status','create_time','modify_time')},
                    'id':intent['plan_id'],'marketing_services_mode':mode}
                p=plan_record(account,info,detail.get('room_info',[]),intent['delivery_entry'],self.clock(),source='plan_detail')
        if p is None:
            selection={'plan_id':intent['plan_id'],'delivery_entry':intent['delivery_entry'],'smart_bid_type':intent['parent_bid_type']}
            refreshed=self.api.read_plans(account,self.api.grant_for_account(account['id']),
                configuration=selection if known.get('configuration_source')=='plan_list' and not needs_effects else None)
            p=refreshed.get('configuration') or parent(self.api,account,intent['plan_id'])
        if p.get('status') in {'DELETE','DELETED','FROZEN'}:raise ValueError('不能调控已删除或冻结计划')
        if p['delivery_entry']!=intent['delivery_entry'] or p['smart_bid_type']!=intent['parent_bid_type'] or p['deep_external_action']!=intent['parent_goal'] or p['budget']!=intent['parent_budget'] or p.get('marketing_services_mode')!=intent.get('parent_marketing_services_mode'):
            raise ValueError('母计划入口或关键配置已变化，请重新准备')
        if p['opt_status']!='ENABLE':raise ValueError('母计划当前没有启用')
        guard=self._guard(intent,account)
        if not guard['allowed']:raise ValueError('；'.join(guard['blocking_reasons']))
        if intent.get('recovery_authority'):return self.budget_recovery.preflight(intent,p,account,token)
        if intent['action'] in {'pause_plan','plan_budget','plan_roi','slowdown_schedule'}:
            if intent['action']=='plan_roi' and p.get('target_roi')!=intent.get('parent_target_roi'):raise ValueError('主计划ROI目标已变化，请重新准备')
            if intent.get('rule_auto_authority') and intent.get('rule_check',{}).get('feature_id')=='f6':self.rule_auto.check_f6(intent,p)
            return {'plan':p}
        task_at=self.clock()
        tasks=active_tasks(self.api,account,p,token,task_at,
            **({'task_id':intent['task_id'],'task_scene':intent['task_scene']} if intent['action']=='pause_task' else {}))
        if intent['action']=='boost':
            if any(t['scene']=='SMART_BOOST' for t in tasks):raise ValueError('本计划已有一键起量，保留原任务，未重复创建')
            return
        if intent['action'] in {'pause_task','task_budget','task_duration'}:
            if intent.get('rule_check',{}).get('feature_id')=='f4':
                from .official_workflow import low_spend_tasks
                tasks=low_spend_tasks(self.api,account,p,token,task_at,tasks,profile)
            rows=[t for t in tasks if t['id']==intent['task_id'] and t['scene']==intent['task_scene']]
            if len(rows)!=1:raise ValueError('任务当前已不在本母计划调控中')
            if intent.get('task_before') and any(rows[0].get(k)!=v for k,v in intent['task_before'].items()):raise ValueError('任务当前预算、时长或出价已变化，请重新准备')
            if intent['action']=='task_budget':
                if rows[0]['scene']=='MATERIAL_ADD_BUDGET':self._budget_guard(p,[t for t in tasks if t['id']!=intent['task_id']],amount(intent['after']),account['id'],intent['plan_id'])
                elif amount(intent['after'])>amount(p['budget']):raise ValueError('任务预算不能超过母计划预算')
            if intent.get('rule_check'):
                from .official_workflow import task_snapshots
                from .cruise_rules import evaluate_account
                effective=copy.deepcopy(profile);effective['features']['f5']['enabled']=False
                workflow={'account_id':account['id'],'plan_id':intent['plan_id'],'captured_at':self.clock().isoformat(),'date':task_at.date().isoformat(),'tasks':tasks}
                checked=evaluate_account(account['id'],effective,task_snapshots(workflow),now=self.clock(),history=[h for h in self.history(account['id']) if h['state'] not in {'draft','checking'}])
                expected=intent['rule_check']
                if not any(all(r.get(k)==v for k,v in expected.items()) for r in checked['proposals']):raise ValueError('确认前原规则条件已不再命中；未提交或改成其他金额')
            return {'tasks':tasks}
        batch=intent['batch']
        if batch['date_end']!=self.clock().date().isoformat():raise ValueError('素材统计窗口已跨日，请重新扫描')
        if batch['rule'].get('optimization_target') in {'综合ROI','综合营销ROI'} and batch['rule']['chase_type']=='控成本追投':
            goals={t['deep_external_action_name'] for t in tasks if t['scene']=='MATERIAL_ADD_BUDGET' and t['smart_bid_type']=='SMART_BID_CUSTOM' and t['roi2_goal'] is not None and t['deep_external_action_name'] in {'支付ROI','净成交ROI'}}
            current=chase_contract(batch['rule'],next(iter(goals)) if len(goals)==1 else None,delivery_entry=p['delivery_entry'],marketing_services_mode=p.get('marketing_services_mode'))
            if current['body']!=intent['native_contract']['body']:raise ValueError('本母计划返回的追投优化目标已变化，未按旧类型提交')
        self._budget_guard(p,tasks,amount(intent['body']['budget']),account['id'],intent['plan_id'])
        active={mid for t in tasks if t['scene']=='MATERIAL_ADD_BUDGET' for mid in t['material_ids']}
        for mid in intent['material_ids']:
            state=material_identity(self.api,account,p,mid,batch['date_start'],batch['date_end'],token)
            if not state['approved']:raise ValueError(f"素材 {mid} 当前审核状态为 {state['audit_status'] or '缺失'}，未提交追投")
            if not state['selectable']:raise ValueError(f"素材 {mid} 当前投放状态为 {state['material_status'] or '缺失'}，未提交追投")
            if mid in active and not batch['allow_running_materials']:raise ValueError(f'素材 {mid} 当前已在调控中，本户未允许重复追投，未提交本批')
        method,query,mapped=material_query(self.api,account,p,batch['date_start'],batch['date_end'],self.clock(),token)
        if mapped:
            query['filters'].append({'field':'material_id','operator':7,'values':intent['material_ids']})
            rows=pages(self.api,method,query,'rows','total_number',token);observed={}
            from .official_monitoring import native
            for r in rows:
                mid=exact_id(native(r.get('dimensions',{}).get('material_id')))
                if mid in observed or exact_id(native(r.get('dimensions',{}).get('anchor_id')))!=mapped[1]:raise ValueError('确认前素材效果身份不唯一')
                observed[mid]=self.api._account_metrics({k:native(r.get('metrics',{}).get(k)) for k in mapped[0]},mapped[0])
        else:
            observed={}
            for mid in intent['material_ids']:
                q=copy.deepcopy(query);q['filtering']['search_keyword']=mid
                rows=pages(self.api,method,q,'ad_material_infos','total_number',token)
                matches=[r for r in rows if exact_id(r.get('material_info',{}).get('video_material',{}).get('material_id'))==mid]
                if len(matches)!=1:raise ValueError('确认前素材效果身份不唯一')
                observed[mid]=self.api._account_metrics(matches[0].get('stats_info',{}))
        if set(observed)!=set(intent['material_ids']):raise ValueError('确认前未取齐本批全部素材的效果')
        rule=batch['rule']
        for metrics in observed.values():
            spend,roi=metrics.get('spend'),metrics.get('roi')
            if spend is None or roi is None or spend<=rule['material_spend_min'] or roi<=rule['material_roi_min'] or rule.get('material_spend_max') is not None and spend>=rule['material_spend_max']:
                raise ValueError('确认前本批素材效果已不符合原规则；未更换素材或自动改金额')
        return {'tasks':tasks}

    def confirm(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'id','intent_digest','confirmed'} or payload['confirmed'] is not True:raise ValueError('须明确核对本单并逐条确认')
        with self.db() as db:kind=db.execute('SELECT intent FROM api_actions WHERE id=?',(payload['id'],)).fetchone()
        if kind and json.loads(kind['intent']).get('execution_channel')=='browser':raise ValueError('此单由网页一次执行，不可转为API重复提交')
        if kind and json.loads(kind['intent']).get('action')=='slowdown_schedule':return self.confirm_schedule(payload)
        # Serialize claims, not status reads. Network does not hold the ledger
        # connection or the short credential/status lock.
        with self.lock:
            with self.db() as db:
                row=db.execute('SELECT * FROM api_actions WHERE id=?',(payload['id'],)).fetchone()
                if not row or row['state']!='draft' or row['digest']!=payload['intent_digest']:raise ValueError('本单已变化或已处理，不重复执行')
                if self.clock()>parse_time(row['expires_at']):raise ValueError('本单超过15分钟核对期，请重新准备')
                intent=json.loads(row['intent']);self._pending(db,intent,row['id'])
                db.execute("UPDATE api_actions SET state='checking',updated_at=?,result=? WHERE id=?",(self.clock().isoformat(),json.dumps({'phase':'preflight_running','message':'正在重新核验当前配置与素材条件；核验通过后仅提交一次。','ad_changes':0},ensure_ascii=False),row['id']))
            try:account=self.account(intent['account_id'])
            except ValueError as exc:
                self._preflight_failed(row['id'],{'phase':'preflight','message':str(exc),'ad_changes':0})
                raise
            with self.api.read_lock:
                try:
                    with self.api.lock:token=self.api._token(self.api.grant_for_account(account['id']))
                    fresh=self._fresh(intent,account,token)
                    # Token may have rotated during a long preflight read.
                    with self.api.lock:token=self.api._token(self.api.grant_for_account(account['id']))
                    with self.gate,self.db() as db:
                        self.conflict_check()
                        latest=self.account(intent['account_id'])
                        if latest.get('delivery_entry')!=account.get('delivery_entry') or latest['scene']!=account['scene']:raise ValueError('本户监测范围已变化，请重新准备')
                        self._pending(db,intent,row['id'])
                        if intent['action']=='material_chase':self.require_allowed_materials(intent['account_id'],intent['material_ids'])
                        if intent.get('auto_authority'):self.auto.verify(intent,(fresh or {}).get('tasks'),exclude=row['id'])
                        if intent.get('rule_auto_authority'):self.rule_auto.verify(intent,(fresh or {}).get('tasks'))
                        if intent.get('recovery_authority'):self.budget_recovery.verify(intent,(fresh or {}).get('tasks'))
                        cursor=db.execute("UPDATE api_actions SET state='submitting',updated_at=? WHERE id=? AND state='checking'",(self.clock().isoformat(),row['id']))
                        if cursor.rowcount!=1:raise ValueError('本单在核对期间已变化，未提交')
                except ValueError as exc:
                    self._preflight_failed(row['id'],{'phase':'preflight','message':str(exc),'ad_changes':0})
                    raise
                except Exception:
                    self._preflight_failed(row['id'],{'phase':'preflight','message':'提交前核验未完成，未发送投放请求；请重新核对。','ad_changes':0})
                    raise
                try:
                    if intent['action']=='slowdown_schedule':return self.schedules.register(row['id'],intent)
                    reply=self.writer(intent['operation'],intent['body'],token)
                except OfficialBusinessError as exc:
                    local=exc.origin.startswith('local_')
                    uncertain=exc.code==51010 and not local
                    # A server-side timeout is not proof that a POST failed.
                    # Persist its ambiguity; only an independent read may
                    # settle an existing task. Never replay this POST.
                    return self._finish(row['id'],'unknown' if uncertain else 'draft' if local else 'rejected',{
                        'message':str(exc),'ad_changes':0,'phase':'submission',
                        'native_code':exc.code,'operation':exc.operation or intent['operation'],
                        'reason':exc.reason,
                        'origin':exc.origin,'request_id':exc.request_id,
                        'post_sent':not local,'submission_uncertain':uncertain,
                        'retryable':local or exc.code==51010 or exc.reason in {'rate_limited','service_busy'},
                        'retry_after_seconds':exc.retry_after})
                except Exception:
                    return self._finish(row['id'],'unknown',{'message':'提交响应未能确认；本单不自动重试，本母计划后续API动作已暂停。'})
                rejection=self.rejected_reply(intent,reply)
                if rejection:return self._finish(row['id'],'rejected',rejection)
                try:
                    stop_at=self._created_stop(row['id'],intent,reply)
                    with prioritize_readback():
                        result=self._readback(intent,reply,account,token)
                    if stop_at:result['auto_stop_at']=stop_at
                    return self._finish(row['id'],'verified',result)
                except Exception:
                    safe_reply={k:reply[k] for k in ('id','task_ids','results') if k in reply}
                    return self._finish(row['id'],'submitted_pending_verification',{'message':'已收到提交响应，独立回读尚未核对一致；不会重试提交。','reply':safe_reply})

    def confirm_schedule(self,payload):
        # Confirmation registers only a future local job, never a platform
        # request. Native configuration, eligibility and boost conflict are
        # checked by APISchedules.tick immediately before the due-time write.
        with self.local_claim_gate():
            with self.db() as db:
                row=db.execute('SELECT * FROM api_actions WHERE id=?',(payload['id'],)).fetchone()
                if not row or row['state']!='draft' or row['digest']!=payload['intent_digest']:raise ValueError('本单已变化或已处理，不重复执行')
                if self.clock()>parse_time(row['expires_at']):raise ValueError('本单超过15分钟核对期，请重新准备')
                intent=json.loads(row['intent']);account=self.account(intent['account_id'])
                p=cached_parent(self.api,account,intent['plan_id'])
                if any(p.get(key)!=intent.get(saved) for key,saved in (('delivery_entry','delivery_entry'),('budget','parent_budget'),('smart_bid_type','parent_bid_type'),('deep_external_action','parent_goal'),('marketing_services_mode','parent_marketing_services_mode'))):raise ValueError('已读取的母计划配置变化，请重新准备排程')
                if p['opt_status']!='ENABLE':raise ValueError('母计划当前没有启用')
                if parse_time(intent['after']['trigger_at'])<=self.clock():raise ValueError('控量开始时点已过，请重新设置下播时间')
                self._pending(db,intent,row['id']);self.conflict_check()
                guard=self._guard(intent,account)
                if not guard['allowed']:raise ValueError('；'.join(guard['blocking_reasons']))
            return self.schedules.register(row['id'],intent)

    def _created_stop(self,rid,intent,reply):
        if not intent.get('auto_stop_hours'):return None
        tid=exact_id(reply.get('id'));due=(self.clock()+timedelta(hours=float(intent['auto_stop_hours']))).isoformat()
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO api_followups VALUES(?,?,?,?,?,?,?,NULL)',(rid+'_stop',rid,intent['account_id'],intent['plan_id'],tid,due,'scheduled'))
            return db.execute('SELECT due_at FROM api_followups WHERE id=?',(rid+'_stop',)).fetchone()[0]

    def tick_followups(self):
        if not self.lock.acquire(blocking=False):return
        try:
            for item in self.followups():
                if item['state']!='scheduled' or parse_time(item['due_at'])>self.clock():continue
                try:
                    account=self.account(item['account_id'])
                    with self.api.read_lock:
                        with self.api.lock:token=self.api._token(self.api.grant_for_account(account['id']))
                        now=self.clock();query={'advertiser_id':int(account['id']),'ad_id':int(item['plan_id']),'marketing_goal':'LIVE_PROM_GOODS',
                            'scene':'MATERIAL_ADD_BUDGET','filtering':{'search_keyword':item['task_id']},'fields':list(TASK_FIELDS),
                            'start_time':now.strftime('%Y-%m-%d 00:00:00'),'end_time':now.strftime('%Y-%m-%d %H:%M:%S')}
                        rows=pages(self.api,'control_tasks',query,'task_list','total_number',token)
                        matches=[r for r in rows if exact_id(r.get('id'))==item['task_id']]
                        if len(matches)!=1:raise ValueError('未取得唯一的原创建任务，未暂停其他任务')
                        if matches[0].get('task_status')=='PROCESSING':
                            with self.gate,self.db() as db:
                                self.conflict_check()
                                cursor=db.execute("UPDATE api_followups SET state='submitting' WHERE id=? AND state='scheduled'",(item['id'],))
                                if cursor.rowcount!=1:continue
                            try:reply=self.writer('pause_control_task',{'advertiser_id':int(account['id']),'task_ids':[int(item['task_id'])],'opt_type':'PAUSE'},token)
                            except Exception:
                                with self.db() as db:db.execute("UPDATE api_followups SET state='unknown',result=? WHERE id=?",(json.dumps({'message':'定时暂停响应未确认，未自动重发。'},ensure_ascii=False),item['id']))
                                continue
                            intent={'action':'pause_task','plan_id':item['plan_id'],'task_id':item['task_id'],'task_scene':'MATERIAL_ADD_BUDGET'}
                            self._readback(intent,reply,account,token)
                        elif matches[0].get('task_status') not in {'DISABLE','FINISH','MANUAL_STOP','MANUAL_FINISH','PAUSE','STOP','COMPLETED'}:raise ValueError('原任务状态尚未明确，不暂停其他任务')
                    with self.db() as db:db.execute("UPDATE api_followups SET state='verified',result=? WHERE id=?",(json.dumps({'message':'定时截止已核对原任务停止；仅操作本单实际创建的任务ID。'},ensure_ascii=False),item['id']))
                except Exception:
                    with self.db() as db:
                        db.execute("UPDATE api_followups SET state=CASE WHEN state='submitting' THEN 'unknown' ELSE state END,result=? WHERE id=?",(json.dumps({'message':'本次定时暂停核验未完成；已发出的请求不重发，未发出时等待接口恢复。'},ensure_ascii=False),item['id']))
        finally:self.lock.release()

    def recheck_followup(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'id'}:raise ValueError('需要具体定时暂停ID')
        with self.lock:
            item=next((f for f in self.followups() if f['id']==payload['id']),None)
            if not item or item['state']!='unknown':raise ValueError('仅对结果不明的定时暂停重新回读')
            account=self.account(item['account_id']);now=self.clock()
            with self.api.read_lock:
                with self.api.lock:token=self.api._token(self.api.grant_for_account(account['id']))
                values=pages(self.api,'control_tasks',{'advertiser_id':int(account['id']),'ad_id':int(item['plan_id']),
                    'marketing_goal':'LIVE_PROM_GOODS','scene':'MATERIAL_ADD_BUDGET','filtering':{'search_keyword':item['task_id']},
                    'fields':list(TASK_FIELDS),'start_time':now.strftime('%Y-%m-%d 00:00:00'),'end_time':now.strftime('%Y-%m-%d %H:%M:%S')},'task_list','total_number',token)
                rows=[r for r in values if exact_id(r.get('id'))==item['task_id']]
                if len(rows)!=1 or rows[0].get('task_status') not in {'DISABLE','FINISH','MANUAL_STOP','MANUAL_FINISH','PAUSE','STOP','COMPLETED'}:raise ValueError('原任务尚未明确停止，保留结果不明状态；本次没有重发暂停')
            with self.db() as db:db.execute("UPDATE api_followups SET state='verified',result=? WHERE id=?",(json.dumps({'message':'原任务已通过独立重新读取核对停止；本次未发出投放请求。'},ensure_ascii=False),item['id']))
            return {'ok':True,'ad_changes':0}

    def _readback(self,intent,reply,account,token):
        aid=int(account['id']);pid=int(intent['plan_id'])
        if intent['action'] in {'plan_budget','plan_roi'}:
            values=reply.get('results',[])
            if len(values)!=1 or exact_id(values[0].get('ad_id'))!=intent['plan_id'] or values[0].get('status')!='SUCCESS':raise ValueError('官方未明确返回本计划修改成功')
            self.api.read_plans(account,self.api.grant_for_account(account['id']))
            value=parent(self.api,account,intent['plan_id'])
            field='budget' if intent['action']=='plan_budget' else 'target_roi'
            if amount(value.get(field))!=amount(intent['after']) or value['delivery_entry']!=intent['delivery_entry'] or value['deep_external_action']!=intent['parent_goal']:raise ValueError('主计划修改后的设置尚未回读一致')
            return {'message':'主计划设置已通过本户官方清单独立回读。','ad_changes':1}
        if intent['action']=='pause_plan':
            results=reply.get('results',[])
            if len(results)!=1 or exact_id(results[0].get('ad_id'))!=intent['plan_id'] or results[0].get('flag') is not True:raise ValueError('未明确返回本计划成功')
            value=self.api.transport('plan_detail',{'advertiser_id':aid,'ad_id':pid},token)
            if exact_id(value.get('ad_id'))!=intent['plan_id'] or value.get('opt_status')!='DISABLE':raise ValueError('主计划未回读暂停')
            return {'message':'本计划已暂停，并通过官方详情独立回读。','ad_changes':1}
        tid=exact_id(reply.get('id')) if intent['action'] in {'material_chase','boost'} else intent['task_id']
        if intent['action'] in {'pause_task','resume_task'} and (reply.get('errors') or [exact_id(v) for v in reply.get('task_ids',[])]!=[tid]):raise ValueError('任务启停结果不完整')
        if intent['action']=='task_budget':
            values=reply.get('results',[])
            if len(values)!=1 or exact_id(values[0].get('task_id'))!=tid or values[0].get('status') is not True:raise ValueError('任务预算修改结果不完整')
        if intent['action']=='task_duration':
            values=reply.get('results',{})
            if values.get('errors') or [exact_id(v) for v in values.get('task_ids',[])]!=[tid]:raise ValueError('任务时长修改结果不完整')
        at=self.clock();query={'advertiser_id':aid,'ad_id':pid,'marketing_goal':'LIVE_PROM_GOODS' if account['scene']=='live' else 'VIDEO_PROM_GOODS',
            'scene':'MATERIAL_ADD_BUDGET' if intent['action']=='material_chase' else 'SMART_BOOST' if intent['action']=='boost' else intent['task_scene'],
            'filtering':{'search_keyword':tid},'fields':list(TASK_FIELDS),'start_time':at.strftime('%Y-%m-%d 00:00:00'),
            'end_time':at.strftime('%Y-%m-%d %H:%M:%S'),'order_type':'DESC','order_field':'stat_cost_for_roi2_assist'}
        rows=pages(self.api,'control_tasks',query,'task_list','total_number',token)
        matches=[r for r in rows if exact_id(r.get('id'))==tid]
        if len(matches)!=1:raise ValueError('回读没有唯一的实际任务ID')
        task=matches[0]
        if intent['action']=='boost':
            if task.get('name')!=intent['body']['name'] or amount(task.get('budget'))!=amount(intent['after']['budget']) or amount(task.get('duration'))!=amount(intent['after']['duration']) or task.get('external_action_name')!='直播间支付':raise ValueError('一键起量预算、时长或目标回读不一致')
        elif intent['action']=='pause_task':
            if task.get('task_status')!='DISABLE':raise ValueError('任务未回读暂停')
        elif intent['action']=='resume_task':
            if task.get('task_status')!='PROCESSING' or amount(task.get('budget'))!=amount(intent['task_before']['budget']):raise ValueError('任务未回读恢复调控或预算已变化')
        elif intent['action'] in {'task_budget','task_duration'}:
            field='budget' if intent['action']=='task_budget' else 'duration'
            if amount(task.get(field))!=amount(intent['after']):raise ValueError('任务新设置尚未回读一致')
        else:
            mids=[exact_id(m.get('material_id')) for m in (task.get('material_list') or [])];body=intent['body']
            if len(mids)!=len(set(mids)) or set(mids)!=set(intent['material_ids']) or amount(task.get('budget'))!=amount(body['budget']) or task.get('smart_bid_type')!=body['smart_bid_type'] or task.get('name')!=body['name']:
                raise ValueError('创建任务的素材、预算、出价或模式回读不一致')
            if body['smart_bid_type']=='SMART_BID_CUSTOM':
                goal={'AD_CONVERT_TYPE_LIVE_PAY_ROI':'支付ROI','AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI':'净成交ROI'}.get(body.get('deep_external_action'))
                if task.get('external_action_name')!='直播间支付' or task.get('deep_external_action_name')!=goal:raise ValueError('创建任务优化目标回读不一致')
                field='roi2_goal' if goal else 'bid'
                if amount(task.get(field))!=amount(body[field]):raise ValueError('创建任务的ROI目标或成交出价回读不一致')
        return {'message':'本单已提交，并按实际任务ID独立回读设置。','task_id':tid,'native_status':task.get('task_status'),'ad_changes':1}

    def _finish(self,rid,state,result):
        with self.db() as db:
            db.execute('UPDATE api_actions SET state=?,updated_at=?,result=? WHERE id=?',(state,self.clock().isoformat(),json.dumps(result,ensure_ascii=False),rid))
            return self.record(db.execute('SELECT * FROM api_actions WHERE id=?',(rid,)).fetchone())

    def _preflight_failed(self,rid,result):
        with self.db() as db:
            db.execute("UPDATE api_actions SET state='draft',updated_at=?,result=? WHERE id=? AND state='checking'",(self.clock().isoformat(),json.dumps(result,ensure_ascii=False),rid))

    def cancel(self,payload,*,reason=None):
        if not isinstance(payload,dict) or set(payload)!={'id'}:raise ValueError('需要具体调控单ID')
        with self.db() as db:kind=db.execute('SELECT intent,state FROM api_actions WHERE id=?',(payload['id'],)).fetchone()
        if kind and kind['state']=='scheduled':
            with self.local_claim_gate(),self.db() as db:
                if self.schedules.cancel(payload['id']):
                    db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=? AND state='scheduled'",(self.clock().isoformat(),payload['id']))
                    return {'ok':True,'ad_changes':0}
                raise ValueError('排程已开始，不能按未提交单取消')
        # Cancelling an unsent intent must not wait behind another account's
        # network preflight. The conditional UPDATE races safely with the final
        # checking->submitting transition: at most one can win. Never include
        # submitting/unknown here, so a potentially sent write stays protected.
        with self.db() as db:
            row=db.execute('SELECT state,result FROM api_actions WHERE id=?',(payload['id'],)).fetchone()
            previous=json.loads(row['result'] or '{}') if row else {}
            message='系统取消未提交单：'+str(reason)[:250] if reason else '用户已取消未提交单；不再提交此单。'
            result={**previous,'phase':'automatic_cancel' if reason else 'manual_cancel','message':message,'ad_changes':0}
            cursor=db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state IN ('draft','checking')",(self.clock().isoformat(),json.dumps(result,ensure_ascii=False),payload['id']))
            if cursor.rowcount!=1:raise ValueError('已提交或结果不明的单请先核验，或跳过该任务；不能当作未提交取消')
        if kind:
            aid=json.loads(kind['intent'])['account_id']
            self.auto.audit(aid,'api_action_cancelled',{'action_id':payload['id'],'message':message,'ad_changes':0})
            self.wake(aid)
        return {'ok':True,'ad_changes':0}

    def recheck(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'id'}:raise ValueError('需要具体API调控单ID')
        resolved=self.resolve_rejected(payload['id'])
        if resolved:return resolved
        with self.lock:
            with self.db() as db:
                row=db.execute('SELECT * FROM api_actions WHERE id=?',(payload['id'],)).fetchone()
                if not row:raise ValueError('没有该API调控单')
                record=self.record(row)
                if not record['readback_available']:
                    raise ValueError('仅支持回读有提交响应的原单及平台超时的任务暂停或预算单')
                intent=record['intent'];result=record['result'] or {}
            account=self.account(intent['account_id'])
            with self.api.read_lock, prioritize_readback():
                with self.api.lock:token=self.api._token(self.api.grant_for_account(account['id']))
                if intent.get('operation')=='browser_rule':return self._readback_browser_rule(row,intent,account,token)
                if row['state']=='unknown':
                    if intent.get('recovery_authority'):return self.budget_recovery.settle_unknown(row,intent,account,token)
                    return self._reconcile_timed_task(row,intent,account,token)
                # Reuses the actual acknowledgement stored before. The caller
                # cannot upload a task ID, response, target value or success flag.
                checked=self._readback(intent,result.get('reply',{}),account,token)
                followup=next((f for f in self.followups() if f['action_id']==row['id']),None)
                if followup:checked['auto_stop_at']=followup['due_at']
                checked['message']='原提交已通过重新读取核对一致；本次未重发投放请求。'
                return self._finish(row['id'],'verified',checked)

    def _readback_browser_rule(self,row,intent,account,token):
        """Read a retired browser rule's exact target through API; never write."""
        if intent['action']=='plan_roi':
            current=fresh_parent(self.api,account,intent['plan_id'])
            if amount(current.get('target_roi'))!=amount(intent['after']):raise ValueError('原主计划ROI尚未通过API回读一致，保留原单')
            task_id=None
        else:
            at=self.clock();day=parse_time(row['created_at']).date().isoformat();tid=exact_id(intent['task_id'])
            values=pages(self.api,'control_tasks',{'advertiser_id':int(account['id']),'ad_id':int(intent['plan_id']),
                'marketing_goal':'LIVE_PROM_GOODS','scene':intent['task_scene'],'filtering':{'search_keyword':tid},
                'fields':list(TASK_FIELDS),'start_time':day+' 00:00:00','end_time':at.strftime('%Y-%m-%d %H:%M:%S')},'task_list','total_number',token)
            matches=[v for v in values if exact_id(v.get('id'))==tid]
            if len(matches)!=1:raise ValueError('原网页调控未取得唯一API任务回读，保留原单')
            t=matches[0]
            if any(t.get(k) is not None and exact_id(t[k])!=v for k,v in [('advertiser_id',account['id']),('ad_id',intent['plan_id'])]):raise ValueError('原网页调控API回读归属不符')
            if t.get('scene') not in (None,intent['task_scene']):raise ValueError('原网页调控API任务类型不符')
            if intent['action']=='pause_task':
                if t.get('task_status') not in {'DISABLE','FINISH','MANUAL_STOP','MANUAL_FINISH','PAUSE','STOP','COMPLETED','FROZEN','DELETED'}:raise ValueError('原暂停单API仍未回读停止状态，保留原单')
            elif intent['action'] in {'task_budget','task_duration'}:
                key='budget' if intent['action']=='task_budget' else 'duration'
                if amount(t.get(key))!=amount(intent['after']):raise ValueError('原网页调控目标值尚未通过API回读一致，保留原单')
            else:raise ValueError('此原单不属于可API回读的任务调控')
            task_id=tid
        return self._finish(row['id'],'verified',{'message':'原网页单目标已通过API独立回读一致；本次未重发修改',
            'task_id':task_id,'before':intent['before'],'after':intent['after'],'independently_verified':True,
            'readback_source':'official_api','no_resubmission':True,'ad_changes':0})

    def _reconcile_timed_task(self,row,intent,account,token):
        """Compare a timed-out task edit with the exact task's native settings."""
        at=self.clock();tid=intent['task_id']
        query={'advertiser_id':int(account['id']),'ad_id':int(intent['plan_id']),
            'marketing_goal':'LIVE_PROM_GOODS' if account['scene']=='live' else 'VIDEO_PROM_GOODS',
            'scene':intent['task_scene'],'filtering':{'search_keyword':tid},'fields':list(TASK_FIELDS),
            'start_time':at.strftime('%Y-%m-%d 00:00:00'),'end_time':at.strftime('%Y-%m-%d %H:%M:%S')}
        queries=[query]
        if intent['action']=='task_budget':
            original_day=parse_time(row['created_at']).date()
            original_query=query
            if original_day<at.date():
                original_query={**query,'start_time':original_day.isoformat()+' 00:00:00',
                    'end_time':original_day.isoformat()+' 23:59:59'}
                queries.append(original_query)
            # The all-status response can omit a deleted task. The explicit
            # native DELETED filter still exposes its last stored budget.
            queries.append({**original_query,'filtering':{'search_keyword':tid,'task_status':'DELETED'}})
        task=None
        for request in queries:
            rows=pages(self.api,'control_tasks',request,'task_list','total_number',token)
            matches=[item for item in rows if exact_id(item.get('id'))==tid]
            if len(matches)>1:raise ValueError('平台超时原单回读到重复任务ID')
            if matches:
                task=matches[0]
                break
        if task is None:raise ValueError('平台超时原单尚未取得唯一任务回读，保留结果不明，未重发')
        if any(task.get(key) is not None and exact_id(task[key])!=expected
                for key,expected in (('advertiser_id',account['id']),('ad_id',intent['plan_id']))):
            raise ValueError('平台超时原单回读的账户或母计划不一致')
        if task.get('scene') is not None and task['scene']!=intent['task_scene']:
            raise ValueError('平台超时原单回读的任务类型不一致')
        original_result=json.loads(row['result'])
        result={'phase':'timeout_readback','task_id':tid,'native_code':51010,'post_sent':True,
            'ad_changes':0,'independently_verified':True,'readback_at':at.isoformat(),
            'native_acknowledged':False,'no_resubmission':True,'request_id':original_result.get('request_id'),
            'observed_status':task.get('task_status')}
        if intent['action']=='task_budget':
            observed=amount(task.get('budget'))
            result['observed_budget']=str(observed)
            if observed==amount(intent['after']):
                return self._finish(row['id'],'verified',{**result,'message':f'原任务预算回读为{observed}元，与原单目标一致。'})
            if observed==amount(intent['before']) and (at-parse_time(row['updated_at'])).total_seconds()>=60:
                return self._finish(row['id'],'rejected',{**result,'retryable':True,
                    'message':f'平台超时后回读预算仍为{observed}元。后续按最新规则和预算重新判断。'})
            raise ValueError(f'原任务回读预算为{observed}元，暂不能确认原单结果')
        if task.get('task_status')=='DISABLE':
            return self._finish(row['id'],'verified',{**result,'message':'平台超时后已独立回读该任务为暂停；本次未重发，无法仅凭回读确认是哪次操作生效。','observed_status':'DISABLE'})
        if task.get('task_status')=='PROCESSING' and (at-parse_time(row['updated_at'])).total_seconds()>=60:
            return self._finish(row['id'],'rejected',{**result,'message':'平台超时至少60秒后回读该任务仍在调控中；原单不重发，后续须重新读取并判断当前规则。','observed_status':'PROCESSING','retryable':True})
        raise ValueError('平台超时原单状态尚未稳定或未明确，继续只读核验，未重发')
