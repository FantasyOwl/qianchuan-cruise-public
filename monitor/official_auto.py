"""Account-scoped standing authority for F5, separate from per-card approval.

Each limit requires a number or an explicit unlimited choice. Enabling the exact
saved profile is separate; scanning alone never grants write authority.
"""
import json
import secrets
import threading
from decimal import Decimal
from .actuation import amount
from .schema import parse_time
from .official_workflow import fresh_parent,digest
from .cruise_rules import _in_window


class QuotaEvidenceMissing(ValueError):
    reason='quota_evidence_missing'


class AutoChase:
    def __init__(self,actions):
        self.a=actions;self.lock=threading.Lock()
        with actions.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS api_auto(account_id TEXT PRIMARY KEY,plan_id TEXT,grant_id TEXT,profile_digest TEXT,profile_revision TEXT,enabled INTEGER,budget_cap TEXT,batch_cap INTEGER,next_at TEXT,state TEXT,message TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS api_auto_attempts(action_id TEXT PRIMARY KEY,account_id TEXT,grant_id TEXT,day TEXT)')
            columns={r['name'] for r in db.execute('PRAGMA table_info(api_auto)')}
            if 'execution_mode' not in columns:db.execute("ALTER TABLE api_auto ADD COLUMN execution_mode TEXT DEFAULT 'automatic'")
            if 'test_run_id' not in columns:db.execute('ALTER TABLE api_auto ADD COLUMN test_run_id TEXT')
            for name in ('budget_unlimited','batches_unlimited'):
                if name not in columns:db.execute(f'ALTER TABLE api_auto ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')

    def status(self):
        with self.a.db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM api_auto')]
        return [{**r,'enabled':bool(r['enabled']),'budget_unlimited':bool(r['budget_unlimited']),
                 'batches_unlimited':bool(r['batches_unlimited']),'day_batches_reserved':self.daily(r['account_id'])} for r in rows]

    @staticmethod
    def limits_complete(row):
        return bool(row and (row.get('budget_unlimited') or row.get('budget_cap'))
                    and (row.get('batches_unlimited') or row.get('batch_cap')))

    def row(self,aid):
        return next((r for r in self.status() if r['account_id']==aid),None)

    def configure(self,payload):
        fields={'account_id','plan_id','profile_revision','enabled','active_budget_cap','max_new_batches_per_day','confirmed'}
        optional={'active_budget_unlimited','daily_batches_unlimited'}
        if not isinstance(payload,dict) or not fields<=set(payload) or set(payload)-fields-optional or type(payload['enabled']) is not bool:raise ValueError('需要本户明确计划、规则版本、自动执行额度和启用确认')
        unlimited=payload.get('active_budget_unlimited',False);unlimited_count=payload.get('daily_batches_unlimited',False)
        if type(unlimited) is not bool or type(unlimited_count) is not bool:raise ValueError('无上限选项必须明确勾选')
        account=self.a.account(payload['account_id']);aid=account['id']
        if account['scene']!='live':raise ValueError('当前全自动素材追投仅面向直播户')
        profile,revision=self.a.profile(aid)
        if payload['profile_revision']!=revision:raise ValueError('本户规则已变化，请重新核对自动执行范围')
        pid=str(payload['plan_id']);bound=self.row(aid)
        # Changing an existing bound account to an unlimited aggregate budget
        # is local authority bookkeeping. The writer still reads and validates
        # the live parent, budget, materials and exact goal before submission.
        p=None if unlimited and bound and bound['plan_id']==pid else fresh_parent(self.a.api,account,pid)
        cap=payload['active_budget_cap'];count=payload['max_new_batches_per_day']
        if unlimited and cap not in (None,'') or unlimited_count and count not in (None,''):raise ValueError('无上限与数值上限不能同时设置')
        cap=None if cap in (None,'') else str(amount(cap))
        if count in (None,''):count=None
        else:
            if isinstance(count,bool) or str(count) not in {str(i) for i in range(1,501)}:raise ValueError('每天最多新增批数须为1至500的整数')
            count=int(count)
        if payload['enabled']:
            if payload['confirmed'] is not True:raise ValueError('启用前须核对本户规则与两项额度，一次授权后才免逐批确认')
            if not self.limits_complete({'budget_cap':cap,'batch_cap':count,'budget_unlimited':unlimited,'batches_unlimited':unlimited_count}):raise ValueError('请自行填写在投预算合计上限及每天最多新增批数，或明确勾选无上限；留空时不会启用全自动')
            if not profile['enabled'] or not profile['features']['f5']['enabled'] or not profile['features']['f5']['rules']:raise ValueError('请先保存并启用本户F5规则')
            if not unlimited and amount(cap)>amount(p['budget'])*Decimal('.3'):raise ValueError('在投预算合计上限不能超过母计划预算30%')
            if not unlimited_count and count>profile['safety']['max_actions_per_day']:raise ValueError('新增批数不能超过本户单日操作上限')
            # A saved grant may coexist with an unsubmitted human draft. The
            # execution loop and final claim still stop behind that draft.
            for rule in profile['features']['f5']['rules']:
                if rule.get('enabled',True) is False:continue
                # The native ROI target is resolved afresh during the scan;
                # this check validates the supplied budget and thresholds only.
                budget=rule.get('daily_budget') if rule['chase_type']=='控成本追投' else rule.get('total_budget')
                if not unlimited and amount(budget)>amount(cap):raise ValueError('单批预算已超过所填在投预算上限')
                if rule.get('material_spend_min') is None or rule.get('material_roi_min') is None:raise ValueError('F5筛选门槛尚未填写完整')
        gid=secrets.token_hex(16)
        with self.a.db() as db:
            db.execute('INSERT OR REPLACE INTO api_auto(account_id,plan_id,grant_id,profile_digest,profile_revision,enabled,budget_cap,batch_cap,next_at,state,message,budget_unlimited,batches_unlimited) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(aid,pid,gid,digest(profile),revision,
                int(payload['enabled']),cap,count,self.a.clock().isoformat(),'enabled' if payload['enabled'] else 'disabled',
                '已按本户规则与额度开启全自动追投，符合条件后无需逐批确认。' if payload['enabled'] else '自动执行设置已保存；尚未启用全自动。',int(unlimited),int(unlimited_count)))
        self.audit(aid,'auto_enabled' if payload['enabled'] else 'auto_settings_saved',{'plan_id':pid,'budget_cap':cap,'batch_cap':count,'budget_unlimited':unlimited,'batches_unlimited':unlimited_count,'ad_changes':0})
        return {'ok':True,'auto':self.row(aid),'ad_changes':0}

    def disable(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'account_id'}:raise ValueError('需要明确关闭哪一户的全自动追投')
        self.a.account(payload['account_id'])
        # This does not acquire the long action/network lock: the final POST
        # gate checks this row again, so disable takes effect during preflight.
        with self.a.db() as db:db.execute("UPDATE api_auto SET enabled=0,state='disabled',message='已关闭全自动；不再创建新追投，已提交任务保留实际记录。' WHERE account_id=?",(payload['account_id'],))
        self.audit(payload['account_id'],'auto_disabled',{'ad_changes':0})
        return {'ok':True,'ad_changes':0}

    def update(self,aid,state,message,*,stop=False):
        with self.a.db() as db:
            db.execute('UPDATE api_auto SET state=?,message=?,enabled=CASE WHEN ? THEN 0 ELSE enabled END WHERE account_id=? AND (enabled=1 OR ?)',(state,message,stop,aid,stop))
        self.audit(aid,'auto_execution',{'state':state,'message':message,'disabled':stop})

    def audit(self,aid,event,detail):
        writer=getattr(self,'audit_writer',None)
        if writer:
            try:writer(aid,event,detail)
            except Exception:pass

    def owned(self,aid,pid=None):
        with self.a.db() as db:
            rows=[self.a.record(r) for r in db.execute('SELECT * FROM api_actions')]
        ids=set()
        for row in rows:
            if row['intent'].get('account_id')!=aid or row['intent'].get('action')!='material_chase':continue
            if pid is not None and row['intent']['plan_id']!=pid:continue
            result=row.get('result') or {};tid=result.get('task_id') or result.get('reply',{}).get('id')
            if tid is not None:ids.add(str(tid))
        return ids

    def daily(self,aid,exclude=None):
        with self.a.db() as db:
            rows=db.execute("SELECT a.action_id,b.state FROM api_auto_attempts a JOIN api_actions b ON a.action_id=b.id WHERE a.account_id=? AND a.day=?",(aid,self.a.clock().date().isoformat())).fetchall()
        return sum(r['action_id']!=exclude and r['state'] in {'draft','checking','submitting','unknown','verified','submitted_pending_verification'} for r in rows)

    def verify(self,intent,tasks,*,exclude=None):
        self.verify_scope(intent,exclude=exclude)
        row=self.row(intent['account_id'])
        if tasks is None:raise ValueError('未取得本户完整在投任务，不能核对自动执行额度')
        if not row.get('budget_unlimited'):self.check_budget(intent,tasks,row['budget_cap'])

    def verify_scope(self,intent,*,exclude=None):
        authority=intent.get('auto_authority');row=self.row(intent['account_id'])
        if not authority or not row or not row['enabled'] or row['grant_id']!=authority['grant_id'] or row['plan_id']!=intent['plan_id']:raise ValueError('本户全自动授权已关闭或已变化，未提交追投')
        if row.get('execution_mode')=='single_test':
            if not row.get('test_run_id') or intent.get('test_run_id')!=row['test_run_id']:raise ValueError('本户仅授权测试一条网页任务，禁止其他追投')
            with self.a.db() as db:
                used=db.execute("SELECT 1 FROM api_actions WHERE json_extract(intent,'$.test_run_id')=? AND id!=? AND state IN ('submitting','unknown','submitted_pending_verification','verified') LIMIT 1",(row['test_run_id'],exclude or '')).fetchone()
            if used:raise ValueError('这一条测试已提交或待核验，不再创建第二条')
        profile,revision=self.a.profile(intent['account_id'])
        if revision!=row['profile_revision'] or digest(profile)!=row['profile_digest']:raise ValueError('本户规则已变化，请重新核对并启用全自动')
        clock=self.a.clock().strftime('%H:%M');protected=profile['broadcast_protection']
        if not profile['enabled'] or not profile['features']['f5']['enabled'] or not _in_window(clock,profile['operation_start'],profile['operation_end']) or protected['enabled'] and _in_window(clock,protected['start'],protected['end']):raise ValueError('当前不在已授权的自动操作时段，或停播保护生效')
        if not self.limits_complete(row):raise ValueError('本户追投额度尚未填写完整或未选择无上限')
        if not row.get('batches_unlimited') and self.daily(intent['account_id'],exclude)>=row['batch_cap']:raise ValueError('已达到本户每天最多新增批数')

    def check_budget(self,intent,tasks,cap,*,allow_lookup=True,replacing_task_id=None):
        tasks=self.a.budget_tasks_with_holds(intent['account_id'],intent['plan_id'],tasks)
        owned=self.owned(intent['account_id'],intent['plan_id'])-{replacing_task_id};total=self.a.isolated_budget(intent['account_id'],intent['plan_id'])
        present={t['id'] for t in tasks}
        for t in tasks:
            if t['id'] in owned and t.get('status','PROCESSING')=='PROCESSING':total+=amount(t['budget'])
        # A just-created task may be visible by ID before it appears in the
        # processing list. Absence is never treated as a released budget.
        for tid in owned-present:
            if not allow_lookup:
                # The browser cannot assume a missing task ended, nor call the
                # very API currently rate limited. Reserve its largest known
                # budget until the original native task is explicitly read.
                total+=self.a.known_task_budget(intent['account_id'],intent['plan_id'],tid);continue
            from .official_workflow import TASK_FIELDS
            from .official_monitoring import pages
            account=self.a.account(intent['account_id']);at=self.a.clock()
            with self.a.api.lock:token=self.a.api._token(self.a.api.grant_for_account(account['id']))
            rows=pages(self.a.api,'control_tasks',{'advertiser_id':int(account['id']),'ad_id':int(intent['plan_id']),
                'marketing_goal':'LIVE_PROM_GOODS','scene':'MATERIAL_ADD_BUDGET','filtering':{'search_keyword':tid},
                'fields':list(TASK_FIELDS),'start_time':at.strftime('%Y-%m-%d 00:00:00'),'end_time':at.strftime('%Y-%m-%d %H:%M:%S')},'task_list','total_number',token)
            found=[t for t in rows if str(t.get('id'))==tid]
            if len(found)!=1:raise QuotaEvidenceMissing(f'本程序已创建任务{tid}未能按ID核实当前状态，保留额度，转网页核验原任务')
            if found[0].get('task_status')=='PROCESSING':total+=amount(found[0].get('budget'))
            elif found[0].get('task_status') not in {'DISABLE','FINISH','MANUAL_STOP','MANUAL_FINISH','PAUSE','STOP','COMPLETED'}:raise QuotaEvidenceMissing(f'本程序已创建任务{tid}状态尚未明确，保留额度，转网页核验原任务')
        if total+amount(intent['body']['budget'])>amount(cap):raise ValueError('在投追投任务预算合计将超过本户全自动额度')

    def tick(self):
        if not self.lock.acquire(blocking=False):return
        try:
            for row in self.status():
                if not row['enabled'] or row.get('execution_mode')!='automatic' or parse_time(row['next_at'])>self.a.clock():continue
                aid=row['account_id'];profile,revision=self.a.profile(aid)
                if revision!=row['profile_revision'] or digest(profile)!=row['profile_digest']:
                    self.update(aid,'blocked','本户规则已改变，已停止全自动；请核对新规则后重新开启。',stop=True);continue
                interval=profile['auto_interval_seconds']
                from datetime import timedelta
                with self.a.db() as db:db.execute('UPDATE api_auto SET next_at=?,state=?,message=? WHERE account_id=?',((self.a.clock()+timedelta(seconds=interval)).isoformat(),'scanning','正在按已确认规则与额度读取本户素材。',aid))
                rid=None
                try:
                    if hasattr(self.a,'hybrid_rules') and self.a.hybrid_rules.active(aid):
                        self.update(aid,'browser_wait','本户规则网页补位进行中，F5等待原单处理，不并行创建。')
                        # This is queue waiting, not a finished material sweep.
                        # Recheck locally soon after the rule lock is released.
                        interval=min(interval,15);continue
                    hybrid=getattr(self.a,'hybrid',None)
                    if hybrid and (hybrid.active(aid,writes_only=True) or hybrid.mode(aid) in {'browser_only','api_data_browser'}):
                        if not hybrid.active(aid):hybrid.request(aid,reason='本户选择API取数、网页追投' if hybrid.mode(aid)=='api_data_browser' else '本户选择仅网页追投')
                        self.update(aid,'browser_wait','网页补位轮次正在读取或执行；API不并行创建相同批次。')
                        continue
                    account=self.a.account(aid)
                    with self.a.lock:
                        # Only retry independent reads of an acknowledged action.
                        pending=[r for r in self.a.status(pending_only=True) if r['intent']['account_id']==aid and r['intent']['plan_id']==row['plan_id'] and not self.a.held(r['intent'])]
                        if pending:
                            if all(r['readback_available'] for r in pending):
                                for original in pending:self.a.recheck({'id':original['id']})
                            else:
                                self.update(aid,'waiting','本计划有待确认、执行中或结果不明的单据，未创建新追投。');continue
                        if not row.get('batches_unlimited') and self.daily(aid)>=row['batch_cap']:
                            self.update(aid,'quota_wait','已达到本户每天最多新增批数，等待下一个自然日；未创建新追投。');continue
                        def progress():
                            current=self.row(aid)
                            if not current or not current['enabled'] or current['grant_id']!=row['grant_id']:raise ValueError('全自动已关闭或授权已变化，停止本轮扫描')
                            # Long full-range scans must still service previously
                            # authorized deadlines between reads.
                            self.a.schedules.tick();self.a.tick_followups()
                            if hasattr(self.a,'rule_auto'):self.a.rule_auto.tick()
                        workflow=self.a.api.scan_workflow(account,profile,row['plan_id'],materials=True,progress=progress)
                        batches=[b for b in workflow['batches'] if b['ready']]
                        if not batches:
                            self.update(aid,'watching',f"本轮消耗门槛范围已扫描完，暂无完整可执行批次；另有{len(workflow.get('unbatched_materials',[]))}条不足一批。");continue
                        completed=0;used=set();duplicates=0
                        for batch in batches:
                            progress()
                            if completed>=profile['safety']['max_actions_per_round']:
                                self.update(aid,'quota_wait',f'本轮已完成{completed}批，达到本户单轮操作上限；剩余{len(batches)-completed}批未创建。');break
                            if used.intersection(batch['material_ids']):continue
                            if not profile['features']['f5'].get('allow_running_materials',False) and set(batch['material_ids'])&self.a.recent_chase_materials(aid,row['plan_id']):
                                duplicates+=1;continue
                            # Refresh the active inventory between batches so
                            # every newly created task counts toward the cap.
                            current=self.a.api.scan_workflow(account,profile,row['plan_id'],materials=False)
                            preview={'account_id':aid,'plan_id':row['plan_id'],'body':batch['native_contract']['body'],'auto_authority':{'grant_id':row['grant_id']}}
                            try:self.verify(preview,current['tasks'])
                            except ValueError as exc:
                                if getattr(exc,'reason',None) in {'rate_limited','quota_evidence_missing'}:raise
                                self.update(aid,'quota_wait',f'本轮已完成{completed}批；{exc}；剩余批次未创建。');break
                            record=self.a.prepare({'account_id':aid,'plan_id':row['plan_id'],'action':'material_chase','batch_key':batch['key']},auto_authority={'grant_id':row['grant_id']})
                            rid=record['id']
                            with self.a.db() as db:db.execute('INSERT INTO api_auto_attempts VALUES(?,?,?,?)',(rid,aid,row['grant_id'],self.a.clock().date().isoformat()))
                            result=self.a.confirm({'id':rid,'intent_digest':record['intent_digest'],'confirmed':True})
                            state=result['state']
                            if state!='verified':
                                interval=max(interval,(result.get('result') or {}).get('retry_after_seconds') or 0)
                                temporary=state!='unknown' and bool((result['result'] or {}).get('retryable'))
                                if state=='draft' and temporary:self.a.cancel({'id':rid})
                                self.update(aid,'verifying' if state=='submitted_pending_verification' else 'waiting' if temporary else 'blocked',
                                    f"本轮已完成{completed}批；"+result['result'].get('message','本批待核验。'),stop=state in {'unknown','rejected'} and not temporary);break
                            completed+=1;used.update(batch['material_ids']);rid=None
                            self.update(aid,'running',f"本轮门槛范围已扫完，已执行并回读{completed}/{len(batches)}批；另有{len(workflow.get('unbatched_materials',[]))}条不足一批。")
                        else:
                            self.update(aid,'running' if completed else 'watching',f'本轮已完成并回读{completed}/{len(batches)}批，跳过含历史已追投素材的{duplicates}批；没有重复创建。')
                except Exception as exc:
                    interval=max(interval,getattr(exc,'retry_after',0))
                    if rid:
                        record=next((r for r in self.a.status() if r['id']==rid),None)
                        if record and record['state']=='draft':
                            if any(s in str(exc) for s in ('提交前素材条件已变化','素材审核或投放状态','本批素材状态')):
                                self.a.note_material_failure(aid,row['plan_id'],record['intent']['material_ids'],str(exc))
                            self.a.cancel({'id':rid},reason=str(exc))
                    hybrid=getattr(self.a,'hybrid',None)
                    if not rid and hybrid and getattr(exc,'reason',None) in {'rate_limited','quota_evidence_missing'}:
                        try:
                            if hybrid.request(aid,reason=str(exc)[:240]):
                                self.update(aid,'browser_wait','API读取限流或原任务额度证据不足，本笔未提交，已请求原Chrome网页核验并补位。')
                                continue
                        except ValueError as blocked:self.update(aid,'waiting',str(blocked)[:300]);continue
                    self.update(aid,'waiting',str(exc)[:300])
                finally:
                    # User interval is the pause after a complete sweep, not a
                    # five-minute deadline that truncates a sweep or overlaps it.
                    with self.a.db() as db:db.execute('UPDATE api_auto SET next_at=? WHERE account_id=? AND grant_id=?',((self.a.clock()+timedelta(seconds=interval)).isoformat(),aid,row['grant_id']))
        finally:self.lock.release()
