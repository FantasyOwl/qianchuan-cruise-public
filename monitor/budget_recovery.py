"""Recover budget-exhausted live tasks through the existing one-POST ledger.

The policy is an explicit user grant, separate from F2's saved ladder. A budget
write and an optional ENABLE write each have their own immutable action. An
uncertain write is resolved by reads, never replayed to advance the chain.
"""
import json
import threading
from datetime import timedelta
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from .actuation import amount
from .cruise_rules import _in_window
from .official_workflow import active_tasks, digest, fresh_parent
from .schema import parse_time


POLICY={'version':1,'enabled':False,'scope':'all_live','roi_basis':'parent_target',
        'increase_ratio':'0.50','cooldown_seconds':300}
PENDING={'draft','checking','submitting','unknown','submitted_pending_verification'}


class BudgetRecovery:
    def __init__(self,actions):
        self.a=actions;self.path=Path(actions.path).parent/'budget-recovery.json'
        self.lock=threading.Lock()
        with self.a.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS api_budget_recovery(account_id TEXT PRIMARY KEY,next_at TEXT,state TEXT,message TEXT,details TEXT)')

    def policy(self):
        if not self.path.exists():return dict(POLICY)
        value=json.loads(self.path.read_text(encoding='utf-8'))
        if (not isinstance(value,dict) or set(value)!=set(POLICY) or type(value['enabled']) is not bool
                or any(value[k]!=v for k,v in POLICY.items() if k!='enabled')):
            raise ValueError('卡预算恢复授权配置无效，未执行')
        return value

    def status(self):
        try:policy=self.policy();error=None
        except (ValueError,OSError) as exc:policy=dict(POLICY);error=str(exc)
        with self.a.db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM api_budget_recovery')]
        return {'policy':policy,'error':error,'accounts':[{**r,'details':json.loads(r['details'])} for r in rows]}

    def scope(self,aid,authority=None):
        policy=self.policy()
        if not policy['enabled'] or authority is not None and authority.get('policy_digest')!=digest(policy):
            raise ValueError('卡预算恢复授权已关闭或变化，未提交')
        account=self.a.account(aid);profile,_=self.a.profile(aid)
        rule_grant=self.a.rule_auto.row(aid)
        if rule_grant and not rule_grant['enabled']:raise ValueError('本户全自动授权已关闭，卡预算恢复等待')
        if account['scene']!='live' or not profile['enabled'] or not self.a.rule_auto.lane_provider(aid):
            raise ValueError('本户不是已开启主巡航的直播户，卡预算恢复等待')
        clock=self.a.clock().strftime('%H:%M');b=profile['broadcast_protection']
        if not _in_window(clock,profile['operation_start'],profile['operation_end']) or b['enabled'] and _in_window(clock,b['start'],b['end']):
            raise ValueError('当前操作时段或停播保护限制卡预算恢复')
        return account,profile

    def records(self,aid,pid=None,tid=None):
        with self.a.db() as db:
            rows=db.execute("SELECT * FROM api_actions WHERE json_extract(intent,'$.account_id')=? AND json_type(intent,'$.recovery_authority') IS NOT NULL ORDER BY created_at DESC,rowid DESC",(aid,)).fetchall()
        return [r for row in rows for r in [self.a.record(row)]
                if (pid is None or r['intent']['plan_id']==pid) and (tid is None or r['intent'].get('task_id')==tid)]

    def read_task(self,account,plan,token,tid,scene):
        rows=active_tasks(self.a.api,account,plan,token,self.a.clock(),task_id=tid,task_scene=scene,task_status=None)
        matches=[t for t in rows if t['id']==tid]
        if len(matches)!=1:raise ValueError('卡预算原任务未唯一回读，不推断已结束或不存在')
        return matches[0]

    def eligible(self,plan,task):
        if plan.get('opt_status')!='ENABLE' or plan.get('status') in {'DELETE','DELETED','FROZEN'}:
            raise ValueError('母计划未开启，不能恢复子任务')
        if task['status']!='OFFLINE_BUDGET':raise ValueError('任务不是预算耗尽状态，不自动启动人工暂停或其他离线任务')
        if plan.get('smart_bid_type')!='SMART_BID_CUSTOM':raise ValueError('母计划没有明确ROI目标，卡预算恢复等待')
        basis=plan.get('target_roi_basis') or {'AD_CONVERT_TYPE_LIVE_PAY_ROI':'overall_payment','AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI':'net'}.get(plan.get('deep_external_action'))
        key={'overall_payment':'roi','net':'net_roi'}.get(basis)
        if key is None:raise ValueError('主计划ROI目标口径不明确，卡预算恢复等待')
        target=amount(plan.get('target_roi'));raw=task['metrics'].get(key)
        if isinstance(raw,bool) or raw is None:raise ValueError('缺少与主计划目标同口径的任务ROI，卡预算恢复等待')
        roi=Decimal(str(raw))
        if not roi.is_finite() or roi<=target:raise ValueError('任务ROI未严格高于主计划设置的ROI目标')
        return {'roi':str(roi),'target_roi':str(target),'roi_basis':basis}

    def build_intent(self,intent,payload,plan,account):
        with self.a.api.read_lock:
            with self.a.api.lock:token=self.a.api._token(self.a.api.grant_for_account(account['id']))
            tid=payload['task_id'];scene=intent['recovery_authority']['task_scene']
            task=self.read_task(account,plan,token,tid,scene)
        intent.update(task_id=tid,task_scene=scene,target_kind='control_task',parent_plan_id=intent['plan_id'],
            recovery_evidence=self.eligible(plan,task),task_before={k:task[k] for k in ('budget','duration','smart_bid_type','roi2_goal','bid')})
        if intent['action']=='resume_task':
            intent.update(before='OFFLINE_BUDGET',after='PROCESSING',operation='pause_control_task',
                body={'advertiser_id':int(account['id']),'task_ids':[int(tid)],'opt_type':'ENABLE'})
        else:
            value=amount(payload.get('value'))
            intent.update(before=str(amount(task['budget'])),after=str(value),operation='update_task_budget',
                body={'advertiser_id':int(account['id']),'update_budget_infos':[{'task_id':int(tid),'budget':float(value)}]})
        self.verify(intent)

    def verify(self,intent,tasks=None):
        self.scope(intent['account_id'],intent.get('recovery_authority') or {})
        auth=intent['recovery_authority']
        if intent['action'] not in {'task_budget','resume_task'} or intent['task_scene'] not in {'SMART_BOOST','MATERIAL_ADD_BUDGET'} or auth.get('task_scene')!=intent['task_scene']:
            raise ValueError('卡预算恢复授权与动作不一致')
        if self.a.held(intent):raise ValueError('该任务已人工跳过，卡预算恢复不执行')
        records=self.records(intent['account_id'],intent['plan_id'],intent['task_id'])
        if intent['action']=='task_budget':
            before=amount(intent['before']);after=(before*Decimal('1.5')).quantize(Decimal('.01'),rounding=ROUND_DOWN)
            if amount(intent['after'])!=after or after<=before:raise ValueError('卡预算恢复必须在原预算基础上提高50%')
            for r in records:
                if r['intent']['action']=='task_budget' and r['state'] not in {'draft','checking','cancelled','expired','rejected'}:
                    if (self.a.clock()-parse_time(r['updated_at'])).total_seconds()<self.policy()['cooldown_seconds']:
                        raise ValueError('卡预算恢复仍在5分钟冷却期')
        else:
            source=next((r for r in records if r['id']==auth.get('budget_action_id')),None)
            if not source or source['intent']['action']!='task_budget' or source['state']!='verified' or source['intent']['profile_digest']!=intent['profile_digest']:
                raise ValueError('尚无当前规则下已核验的原预算单，不发送启动请求')
            if source['result'].get('native_status')=='PROCESSING' or any(r['state']=='verified' and r['intent']['action']=='resume_task' and r['intent']['recovery_authority'].get('budget_action_id')==source['id'] for r in records):
                raise ValueError('原预算恢复链已经完成，不重复启动')
            if amount(intent['task_before']['budget'])!=amount(source['intent']['after']):raise ValueError('加预算后任务金额已变化，未发送启动请求')
            if self.a.clock().date()!=parse_time(source['created_at']).date():raise ValueError('原预算单已跨日，不自动续发启动')
        chase=self.a.auto.row(intent['account_id'])
        if tasks is not None and chase and chase['budget_cap'] is not None and intent['task_id'] in self.a.auto.owned(intent['account_id'],intent['plan_id']):
            # The exhausted task is absent from PROCESSING inventory. Include
            # its full intended budget, not merely the increment, in F5 caps.
            proposed=intent['after'] if intent['action']=='task_budget' else intent['task_before']['budget']
            self.a.auto.check_budget({'account_id':intent['account_id'],'plan_id':intent['plan_id'],
                'body':{'budget':str(proposed)}},tasks,chase['budget_cap'],replacing_task_id=intent['task_id'])

    def preflight(self,intent,plan,account,token):
        if plan.get('target_roi')!=intent.get('parent_target_roi'):raise ValueError('主计划ROI目标已变化，重新巡查后再处理')
        task=self.read_task(account,plan,token,intent['task_id'],intent['task_scene'])
        self.eligible(plan,task)
        if any(task.get(k)!=v for k,v in intent['task_before'].items()):raise ValueError('卡预算任务预算、时长或出价已变化，未提交旧单')
        tasks=active_tasks(self.a.api,account,plan,token,self.a.clock())
        self.verify(intent,tasks)
        budget=amount(intent['after']) if intent['action']=='task_budget' else amount(task['budget'])
        if budget>amount(plan['budget']):raise ValueError('恢复后的任务预算超过母计划预算')
        if task['scene']=='MATERIAL_ADD_BUDGET':self.a._budget_guard(plan,[t for t in tasks if t['id']!=task['id']],budget,account['id'],plan['plan_id'])
        return {'tasks':tasks}

    def settle_unknown(self,row,intent,account,token):
        plan=fresh_parent(self.a.api,account,intent['plan_id'])
        task=self.read_task(account,plan,token,intent['task_id'],intent['task_scene'])
        if intent['action']=='task_budget':matched=amount(task['budget'])==amount(intent['after'])
        else:matched=task['status']=='PROCESSING' and amount(task['budget'])==amount(intent['task_before']['budget'])
        if not matched:raise ValueError('原单结果仍不明确，保持占用，只读等待，不重发')
        return self.a._finish(row['id'],'verified',{'message':'原卡预算恢复单已通过API只读回查核验，未重发。',
            'task_id':task['id'],'native_status':task['status'],'ad_changes':1})

    def run_task(self,account,profile,plan,task,*,limit):
        aid=account['id'];pid=plan['plan_id'];tid=task['id']
        evidence=self.eligible(plan,task);records=self.records(aid,pid,tid)
        if any(r['state'] in PENDING for r in records):raise ValueError('本任务原恢复单待核验，不新增预算或启动单')
        budgets=[r for r in records if r['intent']['action']=='task_budget' and r['state']=='verified']
        source=budgets[0] if budgets else None
        complete=source and (source['result'].get('native_status')=='PROCESSING' or any(r['state']=='verified' and r['intent']['action']=='resume_task' and r['intent']['recovery_authority'].get('budget_action_id')==source['id'] for r in records))
        changes=0
        if not source or complete:
            target=(amount(task['budget'])*Decimal('1.5')).quantize(Decimal('.01'),rounding=ROUND_DOWN)
            record=self.a.prepare({'account_id':aid,'plan_id':pid,'task_id':tid,'action':'task_budget','value':str(target),
                'reason':f'卡预算恢复：任务ROI {evidence["roi"]} > 主计划目标 {evidence["target_roi"]}，原预算增加50%'},
                recovery_authority={'policy_digest':digest(self.policy()),'task_scene':task['scene']})
            source=self.a.confirm({'id':record['id'],'intent_digest':record['intent_digest'],'confirmed':True})
            if source['state']!='verified':return 0
            changes+=1
        if source['result'].get('native_status')=='PROCESSING' or changes>=limit:return changes
        record=self.a.prepare({'account_id':aid,'plan_id':pid,'task_id':tid,'action':'resume_task','reason':'卡预算加预算已独立核验，仍预算离线，启动原任务'},
            recovery_authority={'policy_digest':digest(self.policy()),'task_scene':task['scene'],'budget_action_id':source['id']})
        result=self.a.confirm({'id':record['id'],'intent_digest':record['intent_digest'],'confirmed':True})
        return changes+int(result['state']=='verified')

    def finish(self,aid,interval,state,message,details):
        with self.a.db() as db:db.execute('INSERT OR REPLACE INTO api_budget_recovery VALUES(?,?,?,?,?)',
            (aid,(self.a.clock()+timedelta(seconds=interval)).isoformat(),state,message,json.dumps(details,ensure_ascii=False)))
        self.a.auto.audit(aid,'budget_recovery_scan',{'state':state,'message':message,**details})

    def tick(self):
        if not self.lock.acquire(blocking=False):return
        try:
            if not self.policy()['enabled']:return
            prior={r['account_id']:r for r in self.status()['accounts']}
            for account in self.a.accounts():
                aid=account['id']
                if account['scene']!='live' or not account.get('observe',True):continue
                if aid in prior and parse_time(prior[aid]['next_at'])>self.a.clock():continue
                interval=300;details={'scanned':0,'matched':0,'ad_changes':0,'diagnostics':[]}
                try:
                    account,profile=self.scope(aid);interval=profile['main_interval_seconds']
                    # Resolve only original recovery writes; never approve a draft left by another caller.
                    for r in self.records(aid):
                        if r['state']=='draft':self.a.cancel({'id':r['id']})
                        elif r['readback_available']:self.a.recheck({'id':r['id']})
                    reading=self.a.api.plan_readings.get(aid,{})
                    if reading.get('date')!=self.a.clock().date().isoformat():
                        self.a.api.read_plans(account,self.a.api.grant_for_account(aid));reading=self.a.api.plan_readings.get(aid,{})
                    plans=[p for p in reading.get('plans',[]) if p.get('opt_status')=='ENABLE' and p.get('configuration_source')=='plan_list']
                    if not plans:raise ValueError('本户尚无明确开启的API主计划，等待计划读取')
                    for known in plans:
                        with self.a.api.read_lock:
                            plan=fresh_parent(self.a.api,account,known['plan_id'])
                            with self.a.api.lock:token=self.a.api._token(self.a.api.grant_for_account(aid))
                            tasks=active_tasks(self.a.api,account,plan,token,self.a.clock(),task_status='OFFLINE_BUDGET')
                        details['scanned']+=len(tasks)
                        for task in tasks:
                            before_ids=None
                            try:
                                self.eligible(plan,task);details['matched']+=1
                                if details['ad_changes']>=profile['safety']['max_actions_per_round']:break
                                before_ids={r['id'] for r in self.records(aid,plan['plan_id'],task['id'])}
                                self.run_task(account,profile,plan,task,limit=profile['safety']['max_actions_per_round']-details['ad_changes'])
                            except ValueError as exc:
                                if getattr(exc,'retry_after',0):raise
                                details['diagnostics'].append({'plan_id':plan['plan_id'],'task_id':task['id'],'reason':str(exc)[:300]})
                            finally:
                                if before_ids is not None:details['ad_changes']+=sum(r['id'] not in before_ids and r['state']=='verified' for r in self.records(aid,plan['plan_id'],task['id']))
                    self.finish(aid,interval,'watching',f'已巡查{details["scanned"]}条预算耗尽任务，ROI命中{details["matched"]}条，核验完成{details["ad_changes"]}个API动作。',details)
                except Exception as exc:
                    self.finish(aid,max(interval,getattr(exc,'retry_after',0)),'waiting',str(exc)[:300],details)
        finally:self.lock.release()
