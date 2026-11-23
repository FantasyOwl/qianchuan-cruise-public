"""Rule transport fallback. The API action ledger remains the sole write ledger.

Only an unsubmitted read/preflight or an explicit 40100 rejection can request
fallback. An issued browser permit is never replayed, even after a restart.
"""
import copy
import json
import secrets
import threading
from datetime import timedelta
from decimal import Decimal,InvalidOperation

from .actuation import amount
from .cruise_rules import evaluate_account, _in_window
from .official_workflow import digest
from .schema import parse_time
from .execution_strategy import api_control

OPEN = "state NOT IN ('completed','cancelled')"
MAP = {'control_pause':'pause_task','control_budget_up':'task_budget','control_duration_up':'task_duration',
       'roi_target_up':'plan_roi','roi_target_down':'plan_roi'}

def quota_amount(value):
    if isinstance(value,bool):raise ValueError('平台剩余额度格式无效')
    try:
        if Decimal(str(value))==0:return Decimal(0)
    except InvalidOperation:raise ValueError('平台剩余额度格式无效') from None
    return amount(value)


def supported(version):
    try:return len(version.split('.'))==3 and tuple(map(int,version.split('.'))) >= (1,9,0)
    except (ValueError,AttributeError):return False


class HybridRules:
    def __init__(self, actions):
        self.a=actions;self.lock=threading.RLock()
        with self.a.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS hybrid_rule_runs(id TEXT PRIMARY KEY,account_id TEXT,plan_id TEXT,state TEXT,updated_at TEXT,data TEXT)')

    def now(self):return self.a.clock().isoformat()

    def status(self):
        with self.a.db() as db:rows=db.execute('SELECT * FROM hybrid_rule_runs ORDER BY updated_at DESC').fetchall()
        out=[];seen=set()
        for r in rows:
            if r['account_id'] in seen:continue
            seen.add(r['account_id']);d=json.loads(r['data'])
            out.append({k:r[k] for k in ('id','account_id','plan_id','state','updated_at')}|{
                k:d.get(k) for k in ('phase','message','preview','completed','pages_read','tasks_read','diagnostics','retry_at','paused','action_id')})
        return out

    def active(self,aid):
        if api_control(self.a):return False
        with self.a.db() as db:return bool(db.execute('SELECT 1 FROM hybrid_rule_runs WHERE account_id=? AND '+OPEN,(aid,)).fetchone())

    def retire(self):
        """Retire browser work, preserving every potentially submitted action."""
        with self.a.gate,self.lock,self.a.db() as db:
            for r in db.execute('SELECT * FROM hybrid_rule_runs WHERE '+OPEN).fetchall():
                d=json.loads(r['data']);rid=d.get('action_id')
                if rid:db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state IN ('draft','checking')",
                    (self.now(),json.dumps({'message':'执行分工已切换为API调控，取消未提交网页单','ad_changes':0},ensure_ascii=False),rid))
                d.update(command=None,paused=True,stop_requested=True,api_only_retired=True,message='调控已转API；旧网页单不再执行，结果不明单仅API回读')
                db.execute("UPDATE hybrid_rule_runs SET state='cancelled',updated_at=?,data=? WHERE id=?",(self.now(),json.dumps(d,ensure_ascii=False),r['id']))

    def tick(self):
        if api_control(self.a):self.retire();return
        with self.a.gate,self.lock,self.a.db() as db:
            for r in db.execute('SELECT * FROM hybrid_rule_runs WHERE '+OPEN).fetchall():
                d=json.loads(r['data']);self.recover(db,r,d)

    def scope(self,r,d,*,verify=False):
        account=self.a.account(r['account_id']);p,rev=self.a.profile(account['id'])
        if verify:return account,p
        g=self.a.rule_auto.row(account['id'])
        if not g or not g['enabled'] or g['grant_id']!=d['grant_id'] or g['plan_id']!=r['plan_id']:raise ValueError('六项自动授权已关闭或变化')
        if rev!=d['revision'] or digest(p)!=d['profile_digest']:raise ValueError('规则已变化，请结束本轮后重新扫描')
        if account['scene']!='live' or self.a.hybrid.mode(account['id'])=='api_only':raise ValueError('本户未开启直播网页补位')
        if not d['preview']:
            t=self.a.clock().strftime('%H:%M');b=p['broadcast_protection']
            if not p['enabled'] or not self.a.rule_auto.lane_provider(account['id']):raise ValueError('主巡航或本户规则已关闭')
            if not _in_window(t,p['operation_start'],p['operation_end']) or b['enabled'] and _in_window(t,b['start'],b['end']):raise ValueError('当前处于操作时段限制或停播保护')
        if d['day']!=self.a.clock().date().isoformat():raise ValueError('统计日期已跨天')
        return account,p

    def request(self,aid,*,reason,preview=False,form_preview=False):
        if api_control(self.a):return False
        with self.a.gate,self.lock,self.a.db() as db:
            if self.active(aid):return False
            g=self.a.rule_auto.row(aid)
            if not g or not g['enabled'] or self.a.hybrid.mode(aid)=='api_only':return False
            if self.a.hybrid.active(aid):
                # Rule pauses/budget control must not starve behind an F5
                # sweep that is only reading. An editor or issued action is
                # never preempted. The F5 grant and next schedule are intact.
                pending=db.execute("SELECT * FROM hybrid_runs WHERE account_id=? AND state NOT IN ('completed','cancelled') AND COALESCE(json_extract(data,'$.isolated_readback'),0)!=1",(aid,)).fetchall()
                for old in pending:
                    value=json.loads(old['data'])
                    if value.get('preview') and value.get('material_source')=='official_api':return False
                    if value.get('action_id') or value.get('single_test') or value.get('phase') not in {'tasks','lookup','materials','api_materials'}:return False
                protected=[(old,json.loads(old['data'])) for old in pending if self.a.hybrid.inventory_read_in_progress(json.loads(old['data']))]
                if protected:
                    # A periodic fallback scan must not reset an unfinished
                    # multi-page F5 inventory on every main interval. Finish
                    # this inventory and one batch, then hand back the slot.
                    for old,value in protected:
                        value.setdefault('rule_yield_after_batch',value.get('completed',0)+1)
                        self.a.hybrid.save(db,old,value,old['state'])
                    return False
                for old in pending:
                    value=json.loads(old['data'])
                    if value.get('material_source')=='official_api':
                        value.update(command=None,yielding=True,message='规则调控优先，已保留API候选；本户原调控结束后接着追投。')
                        self.a.hybrid.save(db,old,value)
                    else:
                        value.update(command=None,message='规则调控优先，本轮只读素材扫描让位；F5原授权与额度保留，后续周期恢复')
                        self.a.hybrid.save(db,old,value,'cancelled')
                db.commit()
            self.a._pending(db,{'account_id':aid,'plan_id':g['plan_id'],'action':'auto_rule_scan'})
            p,rev=self.a.profile(aid);rid=secrets.token_hex(16)
            d={'phase':'scan','day':self.a.clock().date().isoformat(),'date_start':self.a.clock().date().isoformat(),
               'grant_id':g['grant_id'],'revision':rev,'profile_digest':digest(p),'preview':preview,'form_preview':bool(preview and form_preview),
               'page':1,'rows':{},'pages_read':0,'tasks_read':0,'completed':0,'diagnostics':[],
               'message':'等待网页读取F1–F4、F6；'+str(reason)[:200],'started_at':self.now()}
            r={'id':rid,'account_id':aid,'plan_id':g['plan_id']};self.scope(r,d)
            db.execute('INSERT INTO hybrid_rule_runs VALUES(?,?,?,?,?,?)',(rid,aid,g['plan_id'],'waiting',self.now(),json.dumps(d,ensure_ascii=False)))
        self.a.auto.audit(aid,'hybrid_rules_started',{'message':d['message'],'ad_changes':0,'preview':preview})
        return True

    def save(self,db,r,d,state='waiting'):
        db.execute('UPDATE hybrid_rule_runs SET state=?,updated_at=?,data=? WHERE id=?',(state,self.now(),json.dumps(d,ensure_ascii=False),r['id']))
        # The main巡航 status must reflect the browser result too. Previously
        # only the API scanner published it, hiding completed/blocked scans
        # behind a stale "waiting for browser" message indefinitely.
        if not d.get('preview'):
            report_state=('watching' if not d.get('completed') else 'running') if state=='completed' else 'waiting_browser'
            db.execute('UPDATE api_rule_auto SET state=?,message=?,diagnostics=? WHERE account_id=? AND grant_id=? AND enabled=1',
                (report_state,d.get('message',''),json.dumps(d.get('diagnostics',[]),ensure_ascii=False),r['account_id'],d['grant_id']))
            if state=='completed':
                self.a.auto.audit(r['account_id'],'hybrid_rules_finished',{'message':d.get('message',''),'tasks_read':d.get('tasks_read',0),
                    'ad_changes':d.get('completed',0),'diagnostics':d.get('diagnostics',[])})

    def control(self,p,operation):
        if not isinstance(p,dict) or set(p)!={'account_id'}:raise ValueError('需要指定本户')
        aid=self.a.account(p['account_id'])['id']
        if operation=='preview':
            if not self.request(aid,reason='用户请求不提交验证',preview=True,form_preview=True):raise ValueError('本户授权未开启，或已有网页轮次，请先处理原轮次')
            return {'ok':True,'ad_changes':0}
        with self.a.gate,self.lock,self.a.db() as db:
            r=db.execute('SELECT * FROM hybrid_rule_runs WHERE account_id=? AND '+OPEN+' ORDER BY updated_at DESC LIMIT 1',(aid,)).fetchone()
            if not r:raise ValueError('本户没有进行中的规则网页补位')
            d=json.loads(r['data']);c=d.get('command')
            readonly=not d.get('action_id') and d['phase'] in {'scan','lifetime'}
            if c and self.a.clock()<parse_time(c['expires_at']) and not readonly:raise ValueError('本次读取仍在进行，请等回传后处理')
            d.update(command=None,paused=False,retry_at=None)
            if operation=='retry' and readonly:
                d.update(phase='scan',page=1,rows={},date_start=d['day'],cycle=d.get('cycle',0)+1)
                d.pop('lifetime',None);d.pop('total',None)
            if operation=='stop':
                d['stop_requested']=True
                if d.get('action_id'):
                    db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=? AND state='draft'",(self.now(),d['action_id']))
                if d.get('permit',{}).get('consumed'):d['phase']='verify';d['message']='已停止新动作，只核验原提交';self.save(db,r,d)
                else:
                    if d.get('action_id'):db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=? AND state='submitting'",(self.now(),d['action_id']))
                    d['message']='用户停止本轮，未提交动作已取消';self.save(db,r,d,'cancelled')
            else:d['message']='已重试当前阶段；结果不明只回读原单';self.save(db,r,d)
        return {'ok':True,'ad_changes':0}

    def profile_changed(self,aid):
        """Discard stale preflights, retain every potentially submitted write."""
        with self.a.gate,self.lock,self.a.db() as db:
            for r in db.execute('SELECT * FROM hybrid_rule_runs WHERE account_id=? AND '+OPEN,(aid,)).fetchall():
                d=json.loads(r['data']);action=db.execute('SELECT state FROM api_actions WHERE id=?',(d.get('action_id'),)).fetchone()
                d.update(command=None,stop_requested=True,retry_at=None,paused=False)
                if d.get('permit',{}).get('consumed') or action and action['state'] in {'submitting','unknown','submitted_pending_verification'}:
                    d.update(phase='verify',message='本户规则已更新，仅核验原提交结果');self.save(db,r,d)
                else:
                    if action and action['state']=='draft':db.execute("UPDATE api_actions SET state='cancelled',updated_at=? WHERE id=?",(self.now(),d['action_id']))
                    d['message']='本户规则已更新，旧版未提交准备已取消';self.save(db,r,d,'cancelled')

    def urgent(self,p):return self.next(p,urgent_only=True)

    def next(self,p,*,urgent_only=False,account_id=None):
        if api_control(self.a):return {'ok':True,'command':None}
        policy=self.a.hybrid.worker(p)
        if not supported(p.get('extension_version')):return {'ok':True,'command':None}
        with self.a.gate,self.lock,self.a.db() as db:
            if self.a.hybrid.preview_ready(db,account_id):return {'ok':True,'command':None}
            r=db.execute('SELECT * FROM hybrid_rule_runs WHERE '+OPEN+" AND (? IS NULL OR account_id=?) AND (? OR json_extract(data,'$.phase') IN ('prepare','verify','dry_prepare')) AND (? OR json_extract(data,'$.preview')=1 OR json_extract(data,'$.phase')='verify') AND COALESCE(json_extract(data,'$.paused'),0)!=1 AND (json_extract(data,'$.retry_at') IS NULL OR json_extract(data,'$.retry_at')<=?) ORDER BY CASE WHEN json_extract(data,'$.phase')='verify' THEN 0 WHEN json_extract(data,'$.phase')='prepare' THEN 1 ELSE 2 END,updated_at LIMIT 1",(account_id,account_id,int(not urgent_only),int(p['armed']),self.now())).fetchone()
            if not r:return {'ok':True,'command':None}
            d=json.loads(r['data'])
            try:
                account,profile=self.scope(r,d,verify=d['phase']=='verify')
                self.recover(db,r,d)
                if d.get('finished'):return {'ok':True,'command':None}
                if not d['preview'] and d['phase']!='verify' and not p['armed']:
                    d['message']='网页执行关闭；等待开启，未修改投放';self.save(db,r,d);return {'ok':True,'command':None}
                c=d.get('command')
                if c and self.a.clock()<parse_time(c['expires_at']) and (c['worker_id'],c['epoch'])!=(p['worker_id'],p['epoch']):return {'ok':True,'command':None}
                if not c or self.a.clock()>=parse_time(c['expires_at']):
                    c={k:p[k] for k in ('worker_id','epoch','extension_version')}
                    c.update(id=secrets.token_hex(16),lease=secrets.token_hex(24),expires_at=(self.a.clock()+timedelta(seconds=120)).isoformat());d['command']=c
                action=db.execute('SELECT * FROM api_actions WHERE id=?',(d.get('action_id'),)).fetchone()
                # Scan every running task page, not thousands of ended tasks.
                # Readback searches the exact original ID across all statuses.
                # Workflow stages never become material-view router names.
                readback=d['phase']=='verify'
                intent=json.loads(action['intent']) if action else {}
                out={**c,'run_id':r['id'],'rule_job':True,'rule_phase':d['phase'],'phase':'lookup' if readback else 'tasks','lookup_id':intent.get('task_id','') if readback else '',
                     'account_id':r['account_id'],'plan_id':r['plan_id'],'group':account['tab_group'],'page':d['page'],'page_size':d.get('page_size'),
                     'date_start':d['date_start'],'date_end':d['day'],'rule_range':True,'preview':d['preview'],
                     'document_cycle':d.get('cycle',0),'action':self.a.record(action) if action else d.get('preview_action')}
                if out['action']:
                    # Pagination belongs to the worker command, never to the
                    # native row's immutable field comparison.
                    out['action']=copy.deepcopy(out['action'])
                    out['action']['intent']['browser_surface'].pop('page',None)
                self.save(db,r,d,'working');return {'ok':True,'command':out}
            except ValueError as e:
                d['message']=str(e);d['paused']=True;self.save(db,r,d)
                return {'ok':True,'command':None,'message':str(e)}

    def recover(self,db,r,d):
        if not d.get('action_id') and (d.get('stop_requested') or d['revision']!=self.a.profile(r['account_id'])[1] or d['day']!=self.a.clock().date().isoformat()):
            d.update(finished=True,command=None,message='已结束旧日期或旧规则的未提交轮次，下一轮按最新日期和保存规则重新检查')
            self.save(db,r,d,'cancelled');return
        if d['phase']=='verify' and d.get('paused') and d.get('retry_at') and self.a.clock()>=parse_time(d['retry_at']):
            d.update(paused=False,failures=0,command=None,message='自动恢复原单只读核验；先清理可证明归属的失败表单，不重发提交')
            self.save(db,r,d)
        if d.get('action_id'):
            row=db.execute('SELECT * FROM api_actions WHERE id=?',(d['action_id'],)).fetchone()
            if row and self.a.held(json.loads(row['intent'])):
                d.update(finished=True,message='本任务已人工跳过；保留原提交记录，结束本轮补位');self.save(db,r,d,'cancelled');return
            # An unsubmitted failed editor must not lock F5 forever. Unknown
            # or consumed submissions retain their original readback lock.
            if row and row['state'] in {'draft','expired','cancelled','rejected'} and not d.get('permit',{}).get('consumed'):
                expired=self.a.clock()>=parse_time(row['expires_at'])
                if expired or d.get('paused') or row['state'] in {'expired','cancelled','rejected'}:
                    message='原规则单未提交且已过期或连续准备失败，自动结束旧草稿并释放本户；F5可继续，规则下轮重新判断。'
                    db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state='draft'",(self.now(),json.dumps({'message':message,'ad_changes':0},ensure_ascii=False),row['id']))
                    d.update(finished=True,command=None,message=message);self.save(db,r,d,'cancelled')
                    interval=self.a.profile(r['account_id'])[0]['main_interval_seconds']
                    db.execute("UPDATE api_rule_auto SET next_at=?,state='waiting',message=? WHERE account_id=? AND grant_id=? AND enabled=1",
                        ((self.a.clock()+timedelta(seconds=interval)).isoformat(),message,r['account_id'],d['grant_id']))
                    db.execute("UPDATE api_auto SET next_at=? WHERE account_id=? AND enabled=1 AND state='browser_wait'",(self.now(),r['account_id']))
                    self.a.auto.audit(r['account_id'],'hybrid_rule_unsubmitted_released',{'message':message,'action_id':row['id'],'ad_changes':0})
                    return
        if not d.get('action_id') and (self.a.clock()-parse_time(d['started_at'])).total_seconds()>1200:
            d.update(finished=True,message='本轮读取超过20分钟，停止等待，下一周期重新扫描');self.save(db,r,d,'cancelled');return
        permit=d.get('permit')
        if permit and not permit['consumed'] and self.a.clock()>=parse_time(permit['expires_at']):
            db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state='submitting'",(self.now(),json.dumps({'message':'最终许可未消费，未授权点击','ad_changes':0}),d['action_id']))
            d.update(finished=True,message='最终许可未消费，取消原单，下一周期重新判断');self.save(db,r,d,'cancelled')

    def bound(self,db,p):
        r=db.execute('SELECT * FROM hybrid_rule_runs WHERE id=?',(p.get('run_id'),)).fetchone()
        if not r or r['state'] in {'completed','cancelled'}:raise ValueError('规则网页轮次已结束')
        d=json.loads(r['data']);c=d.get('command') or {}
        if p.get('id')!=c.get('id') or not secrets.compare_digest(str(p.get('lease','')),c.get('lease','')):raise ValueError('规则网页命令身份不符')
        if self.a.clock()>=parse_time(c['expires_at']):raise ValueError('规则网页命令过期')
        self.scope(r,d,verify=d['phase']=='verify')
        return r,d

    def observation(self,v,r,d):
        if not isinstance(v,dict):raise ValueError('缺少原页面观察')
        base={k:x for k,x in v.items() if k not in {'rule_rows','plan_surface','validation_rejection'}}
        self.a.hybrid.observation(base,r)
        from .delivery_entry import report_entries
        if v['delivery_entry'] not in report_entries(self.a.account(r['account_id'])):raise ValueError('当前账户未选择此投放入口')
        if (v['date_start'],v['date_end'],v['page'])!=(d['date_start'],d['day'],d['page']):raise ValueError('统计范围或页码不符')
        rows=v.get('rule_rows')
        if not isinstance(rows,list) or len(rows)!=len(v['rows']):raise ValueError('任务明细未完整加载')
        if {s.get('task_id') for s in rows}!={s['id'] for s in v['rows']}:raise ValueError('任务行身份不一致')
        for s in rows:
            if (s.get('account_id'),s.get('plan_id'))!=(r['account_id'],r['plan_id']):raise ValueError('任务跨账户或母计划')
            if s.get('running') is not None and type(s['running']) is not bool:raise ValueError('任务状态结构无效')
            if not 0<=(self.a.clock()-parse_time(s.get('captured_at'))).total_seconds()<=90:raise ValueError('任务行采集时间过期')
            if s.get('budget') is not None:amount(s['budget'])
            if not isinstance(s.get('metrics'),dict) or not isinstance(s.get('context'),dict) or not isinstance(s.get('actions'),list):raise ValueError('任务指标或设置结构无效')
        plan=v.get('plan_surface')
        if plan and (plan.get('account_id'),plan.get('plan_id'))!=(r['account_id'],r['plan_id']):raise ValueError('主计划ROI字段身份不符')
        return v

    def snapshots(self,r,d):
        out=[]
        for s in d['rows'].values():
            ctx=copy.deepcopy(s['context']);ctx['parent_plan_id']=r['plan_id']
            metrics=copy.deepcopy(s['metrics']);metrics['budget']=s['budget']
            life=d.get('lifetime',{}).get(s['task_id'])
            if life:
                ctx.update(lifetime_spend_verified=True,lifetime_spend_start=d['lifetime_start']+'T00:00:00+08:00',lifetime_spend_end=life['captured_at'])
                metrics['lifetime_spend']=life['metrics'].get('spend')
            out.append({'id':'browser-rule:'+s['task_id']+':'+s['captured_at'],'account_id':r['account_id'],'entity_id':s['task_id'],
                'scope':'control','scene':'live','metric_basis':s['metric_basis'],'context':ctx,'metrics':metrics,'control':{'running':s['running']},
                'source':{'kind':'browser_dom','captured_at':s['captured_at'],'metric_date':d['day'],'parent_entity_id':r['plan_id']}})
        return out

    def candidates(self,r,d):
        account,p=self.scope(r,d);effective=copy.deepcopy(p)
        effective['features']['f5']['enabled']=False;effective['features']['f6']['enabled']=False
        held={h['task_id'] for h in self.a.holds(r['account_id'],r['plan_id'])}
        part=evaluate_account(r['account_id'],effective,[s for s in self.snapshots(r,d) if s['entity_id'] not in held],now=self.a.clock(),history=[h for h in self.a.history(r['account_id']) if h['state'] not in {'draft','checking'}])
        plan=d.get('plan') or {};f6,reason=self.a.rule_auto.f6_candidate(r['account_id'],r['plan_id'],p,plan)
        d['diagnostics']=part['skipped'][:30]+([{'feature_id':'f6','reason':reason}] if reason else [])
        allowed=[]
        for proposal in part['proposals']:
            surface=d['rows'].get(proposal['entity_id'],{});ctx=surface.get('context',{})
            available=ctx.get('native_available_budget')
            if proposal['action']=='control_budget_up' and ctx.get('control_delivery_mode')=='cost_control' and available is not None and amount(proposal['after'])-amount(proposal['before'])>quota_amount(available):
                d['diagnostics'].append({'feature_id':proposal['feature_id'],'entity_id':proposal['entity_id'],'reason':f'平台可追加预算仅{available}元，不足本条增加额；先执行已启用的暂停规则，再读额度'})
                continue
            allowed.append(proposal)
        return sorted(allowed,key=lambda x:0 if x['action']=='control_pause' else 1)+([f6] if f6 else [])

    def guard(self,r,d,i):
        account,p=self.scope(r,d)
        self.a.rule_auto.verify(i,[{'id':s['task_id'],'budget':s['budget'],'status':'DISABLE' if s['running'] is False else 'PROCESSING','scene':'MATERIAL_ADD_BUDGET'} for s in d['rows'].values()],browser_inventory=True)
        if self.a.held(i):raise ValueError('本任务已人工跳过')
        guard=self.a._guard(i,account)
        if not guard['allowed']:raise ValueError('；'.join(guard['blocking_reasons']))
        if i['action']=='task_budget':
            after=amount(i['after']);parent=amount(d['parent_budget'])
            ctx=d['rows'].get(i['task_id'],{}).get('context',{});available=ctx.get('native_available_budget')
            if ctx.get('control_delivery_mode')=='cost_control' and available is not None and after-amount(i['before'])>quota_amount(available):raise ValueError('平台可追加预算不足，本条未提交；先处理暂停任务再重读额度')
            if after>parent:raise ValueError('任务预算不能超过母计划预算')
            # Retain the same 30% mother-plan task budget boundary as API.
            inventory=[{'id':s['task_id'],'budget':s['budget'],'status':'DISABLE' if s['running'] is False else 'PROCESSING'} for s in d['rows'].values()]
            inventory=self.a.budget_tasks_with_holds(r['account_id'],r['plan_id'],inventory)
            if sum(amount(s['budget']) for s in inventory if s.get('status')!='DISABLE' and s['id']!=i['task_id'])+after+self.a.isolated_budget(r['account_id'],r['plan_id'])>parent*Decimal('.3'):raise ValueError('在投追投预算合计及后台核验保留额超过母计划预算30%')
        return guard

    def make_intent(self,r,d,proposal):
        s=d.get('plan') if proposal['feature_id']=='f6' else d['rows'][proposal['entity_id']]
        if proposal['action'] not in s.get('actions',[]):raise ValueError('原页面缺少本任务的明确编辑入口')
        fid=proposal['feature_id'];kind=MAP[proposal['action']]
        after=proposal['after'];before=proposal['before']
        if kind=='task_duration':before=str(amount(before)/60);after=str(amount(after)/60)
        if kind=='task_duration' and (not Decimal('1.5')<=amount(after)<=24 or amount(after)%Decimal('.5')):raise ValueError('原生固定时长仅支持1.5至24小时，步进0.5小时')
        i={'account_id':r['account_id'],'account_name':self.a.account(r['account_id']).get('name',r['account_id']),
           'plan_id':r['plan_id'],'parent_plan_id':r['plan_id'],'scene':'live','plan_name':'计划'+r['plan_id'],
           'action':kind,'before':before,'after':after,'execution_channel':'browser','reason':'网页补位命中'+fid.upper()+'已保存规则',
           'profile_revision':d['revision'],'profile_digest':d['profile_digest'],'delivery_entry':d['delivery_entry'],
           'parent_budget':d['parent_budget'],'parent_bid_type':'SMART_BID_CUSTOM','body':{},'operation':'browser_rule',
           'target_kind':'main_plan' if fid=='f6' else 'control_task','rule_auto_authority':{'grant_id':d['grant_id']},
           'rule_check':{k:proposal[k] for k in ('feature_id','entity_id','action','before','after')},'browser_surface':copy.deepcopy(s)}
        if fid!='f6':i.update(task_id=proposal['entity_id'],task_scene='MATERIAL_ADD_BUDGET')
        i['cost_guarantee']=self.guard(r,d,i)
        return i

    def draft(self,db,r,d,proposal):
        i=self.make_intent(r,d,proposal);s=i['browser_surface'];fid=i['rule_check']['feature_id']
        self.pending(db,r,d,i);rid=secrets.token_hex(12)
        db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,NULL)',(rid,'draft',json.dumps(i,ensure_ascii=False),digest(i),self.now(),(self.a.clock()+timedelta(minutes=15)).isoformat(),self.now()))
        d.update(action_id=rid,phase='prepare',page=s.get('page',1),date_start=d['day'],command=None,message='已准备'+fid.upper()+'网页补位单，等待核对原任务')

    def pending(self,db,r,d,intent,exclude=None):
        # A single-page result and the refreshed pre-submit observation are
        # still in memory here. Publish their validated inventory in this same
        # transaction before the isolation guard reads its native evidence.
        db.execute('UPDATE hybrid_rule_runs SET data=? WHERE id=?',(json.dumps(d,ensure_ascii=False),r['id']))
        self.a._pending(db,intent,exclude,hybrid_rule_run=r['id'])

    def result(self,p):
        if not isinstance(p,dict) or set(p)!={'run_id','id','lease','ok','message','observation'} or type(p['ok']) is not bool:raise ValueError('规则回传字段无效')
        with self.a.gate,self.lock,self.a.db() as db:
            r,d=self.bound(db,p)
            if not p['ok']:
                stale=d['phase'] in {'prepare','dry_prepare'} and str(p['message']).startswith(('原任务设置已变化','当前页未找到原任务','全量预算清单已过期','提交前规则或目标值已变化'))
                if stale and d.get('stale_resets',0)<3:
                    # No begin permit was issued. A changed setting invalidates
                    # this draft; do not keep applying an old absolute target.
                    rid=d.get('action_id')
                    if rid:db.execute("UPDATE api_actions SET state='cancelled',updated_at=?,result=? WHERE id=? AND state='draft'",(self.now(),json.dumps({'message':'原设置变化，未提交旧单，重新读取规则','ad_changes':0},ensure_ascii=False),rid))
                    d.update(phase='scan',page=1,rows={},date_start=d['day'],command=None,paused=False,retry_at=None,
                        stale_resets=d.get('stale_resets',0)+1,message='旧设置已变化，未提交；已重新安排全量规则检查。'+str(p['message'])[:180])
                    for key in ('action_id','preview_action','lifetime','total'):d.pop(key,None)
                    self.save(db,r,d);return {'ok':True,'ad_changes':0}
                d['failures']=d.get('failures',0)+1;d['paused']=d['failures']>=5
                d.update(message=str(p['message'])[:350],command=None,retry_at=(self.a.clock()+timedelta(seconds=min(300,30*d['failures']))).isoformat())
                if d['phase']=='verify':d['cycle']=d.get('cycle',0)+1
                if d['phase'] in {'scan','lifetime'}:
                    d.update(phase='scan',page=1,rows={},date_start=d['day'],cycle=d.get('cycle',0)+1)
                    d.pop('total',None);d.pop('lifetime',None)
                self.save(db,r,d);return {'ok':True,'ad_changes':0}
            v=self.observation(p['observation'],r,d);d.update(command=None,failures=0,retry_at=None)
            if d['phase']=='dry_prepare':
                d['message']='原任务表单已核对并取消；本轮没有提交投放修改'
                self.save(db,r,d,'completed');return {'ok':True,'ad_changes':0}
            if d['phase']=='verify':
                row=db.execute('SELECT * FROM api_actions WHERE id=?',(d['action_id'],)).fetchone();i=json.loads(row['intent'])
                if not d.get('permit',{}).get('consumed') or v['document_id']==d['submitted_document'] or parse_time(v['captured_at'])<=parse_time(d['begin_at']):raise ValueError('需要刷新后独立核验原任务')
                target=v.get('plan_surface') if i['action']=='plan_roi' else next((s for s in v['rule_rows'] if s['task_id']==i['task_id']),None)
                field={'pause_task':'running','task_budget':'budget','task_duration':'duration_minutes','plan_roi':'target_roi'}[i['action']]
                expected=False if i['action']=='pause_task' else str(amount(i['after'])*(60 if i['action']=='task_duration' else 1))
                actual=target.get(field) if target else None
                proof=v.get('validation_rejection')
                if proof and i['action']=='task_budget' and target and amount(actual)==amount(i['before']):
                    if (proof.get('kind'),proof.get('task_id'),proof.get('name'),proof.get('material_count'))!=('native_budget_insufficient',i['task_id'],i['browser_surface']['name'],i['browser_surface']['material_count']) or i['browser_surface']['context'].get('control_delivery_mode')!='cost_control':raise ValueError('原失败表单身份未核对一致')
                    proof_at=parse_time(proof.get('captured_at'))
                    if not parse_time(d['begin_at'])<=proof_at<=parse_time(v['captured_at']) or (self.a.clock()-proof_at).total_seconds()>86400 or amount(proof.get('before'))!=amount(i['before']) or amount(proof.get('after'))!=amount(i['after']) or amount(i['after'])-amount(i['before'])<=quota_amount(proof.get('available')) or proof.get('message')!='不得超过当前可用预算，请重新填写':raise ValueError('缺少平台预算校验失败证据及新鲜的独立回读')
                    result={'message':f'平台表单因可追加预算不足拒绝修改；刷新后仍为{i["before"]}元，未加到{i["after"]}元。已关闭原表单，继续其他规则。','task_id':i['task_id'],'before':i['before'],'after':i['after'],'ad_changes':0,'submission_uncertain':False,'reason':'native_budget_insufficient','validation_rejection':proof}
                    db.execute("UPDATE api_actions SET state='rejected',updated_at=?,result=? WHERE id=? AND state IN ('submitting','unknown')",(self.now(),json.dumps(result,ensure_ascii=False),row['id']))
                    self.a.auto.audit(r['account_id'],'hybrid_rule_native_rejected',result|{'action_id':row['id']})
                    d.update(phase='scan',rows={},page=1,date_start=d['day'],cycle=d.get('cycle',0)+1,started_at=self.now(),message=result['message'])
                    for key in ('action_id','permit','lifetime'):d.pop(key,None)
                    self.save(db,r,d);return {'ok':True,'ad_changes':0}
                if i['action']=='pause_task' and target and target.get('status') not in {'已暂停','调控暂停','暂停投放'}:actual=None
                if actual is None and v['has_next']:
                    d['page']+=1;self.save(db,r,d);return {'ok':True,'ad_changes':0}
                if actual is None and d['page']>1:d['page']=1
                if actual is None or (actual is not False if expected is False else amount(actual)!=amount(expected)):raise ValueError('原任务回读尚未与目标值一致；保持待核验，不重发')
                result={'message':'网页修改后独立回读一致','task_id':i.get('task_id'),'before':i['before'],'after':i['after'],'ad_changes':1,'execution_channel':'browser'}
                db.execute("UPDATE api_actions SET state='verified',updated_at=?,result=? WHERE id=? AND state IN ('submitting','unknown')",(self.now(),json.dumps(result,ensure_ascii=False),row['id']))
                d['completed']+=1;d['message']='已完成并核验'+i['rule_check']['feature_id'].upper()+'：'+str(i['before'])+' → '+str(i['after'])
                self.a.auto.audit(r['account_id'],'hybrid_rule_verified',result|{'action_id':row['id'],'action':i['action']})
                # A new full scan is required for every subsequent action.
                d.update(phase='scan',rows={},page=1,date_start=d['day'],cycle=d.get('cycle',0)+1);d.pop('action_id',None);d.pop('permit',None);d.pop('lifetime',None)
                if d.get('stop_requested') or d['completed']>=self.a.profile(r['account_id'])[0]['safety']['max_actions_per_round']:self.save(db,r,d,'completed')
                else:self.save(db,r,d)
                return {'ok':True,'ad_changes':1}
            rows={s['task_id']:{**s,'page':v['page']} for s in v['rule_rows']}
            key='lifetime' if d['phase']=='lifetime' else 'rows'
            old=d.setdefault(key,{})
            if set(old)&set(rows):raise ValueError('翻页重复任务，未使用重复数据')
            if d.get('total') is not None and d['total']!=v['total']:raise ValueError('扫描中任务总数改变，请重试完整扫描')
            d['total']=v['total'];old.update(rows);d['pages_read']+=1;d['tasks_read']=len(d['rows']);d['page_size']=v['page_size']
            d.update(parent_budget=v['parent_budget'],delivery_entry=v['delivery_entry'],plan=v.get('plan_surface'))
            if v['has_next']:d['page']+=1;self.save(db,r,d);return {'ok':True,'ad_changes':0}
            if len(old)!=v['total']:raise ValueError('未完成全部任务分页')
            d.pop('total',None)
            p0=self.a.profile(r['account_id'])[0]
            if d['phase']=='scan' and p0['features']['f4']['enabled'] and p0['features']['f4'].get('pause_rule') in {'low_spend','either'}:
                dates=[s['context'].get('created_at') for s in d['rows'].values() if s['context'].get('created_at')]
                if dates:
                    start=min(parse_time(x).date().isoformat() for x in dates)
                    d.update(phase='lifetime',lifetime={},lifetime_start=start,date_start=start,page=1);self.save(db,r,d);return {'ok':True,'ad_changes':0}
            proposals=self.candidates(r,d)
            if d['preview']:
                if d.get('form_preview') and proposals:
                    for proposal in proposals:
                        try:
                            i=self.make_intent(r,d,proposal);d['preview_action']={'id':'preview_'+r['id'],'intent':i}
                            d.update(phase='dry_prepare',page=i['browser_surface'].get('page',1),date_start=d['day'],message='已命中规则，等待原表单核对后取消；不提交广告')
                            self.save(db,r,d);return {'ok':True,'ad_changes':0}
                        except ValueError as exc:d['diagnostics'].append({'feature_id':proposal['feature_id'],'reason':str(exc)})
                d['message']=f'只读验证完成：完整扫描{len(d["rows"])}条任务，命中{len(proposals)}个动作；未提交修改'
                d['candidates']=proposals;self.save(db,r,d,'completed')
            elif proposals:
                for proposal in proposals:
                    try:self.draft(db,r,d,proposal);break
                    except ValueError as exc:d['diagnostics'].append({'feature_id':proposal['feature_id'],'entity_id':proposal['entity_id'],
                        'before':proposal['before'],'after':proposal['after'],'reason':str(exc)})
                else:
                    blockers=list(dict.fromkeys(x['reason'] for x in d['diagnostics'] if x.get('reason') and x.get('entity_id')))
                    d['message']=f'已完整检查{len(d["rows"])}条任务，命中{len(proposals)}条规则，本轮未修改：'+('；'.join(blockers[:3]) or '当前页面或保价检查未通过')
                    self.save(db,r,d,'completed');return {'ok':True,'ad_changes':0}
                self.save(db,r,d)
            else:d['message']='全量任务已核对，本轮没有可执行的规则动作；详见各项原因';self.save(db,r,d,'completed')
        return {'ok':True,'ad_changes':0}

    def begin(self,p):
        if api_control(self.a):raise ValueError('暂停、预算、时长及ROI调控统一通过API，网页许可已关闭')
        if not isinstance(p,dict) or set(p)!={'run_id','id','lease','action_id','observation'}:raise ValueError('规则许可字段无效')
        self.a.conflict_check()
        with self.a.gate,self.lock,self.a.db() as db:
            r,d=self.bound(db,p)
            if d['phase']!='prepare' or d['preview'] or p['action_id']!=d.get('action_id'):raise ValueError('本轮没有提交授权')
            row=db.execute('SELECT * FROM api_actions WHERE id=?',(d['action_id'],)).fetchone()
            if not row or row['state']!='draft' or self.a.clock()>=parse_time(row['expires_at']):raise ValueError('原单已变化或过期')
            client=db.execute('SELECT * FROM hybrid_clients WHERE worker_id=?',(d['command']['worker_id'],)).fetchone()
            if not client or not client['armed'] or not supported(client['version']) or not 0<=(self.a.clock()-parse_time(client['seen_at'])).total_seconds()<=120:raise ValueError('原Chrome网页执行已关闭或连接过期')
            i=json.loads(row['intent']);v=self.observation(p['observation'],r,d)
            fresh={s['task_id']:s for s in v['rule_rows']}
            if v['page']==1 and not v['has_next'] and len(fresh)==v['total']:
                if set(fresh)!=set(d['rows']):raise ValueError('原任务设置已变化：在投任务清单变化，重新全量检查')
                # A complete fresh native inventory replaces the old scan;
                # never merely renew timestamps or relax the 90s boundary.
                d['rows']={tid:{**surface,'page':1} for tid,surface in fresh.items()}
            if i.get('task_id'):
                if i['task_id'] not in fresh:raise ValueError('未找到唯一原任务')
                d['rows'][i['task_id']]=fresh[i['task_id']]
            d['plan']=v.get('plan_surface');d['parent_budget']=v['parent_budget']
            if not any({k:x[k] for k in i['rule_check']}==i['rule_check'] for x in self.candidates(r,d)):raise ValueError('提交前规则或目标值已变化')
            # Every row used for quota must still be recent, not an old partial sweep.
            if any(not 0<=(self.a.clock()-parse_time(s['captured_at'])).total_seconds()<=90 for s in d['rows'].values()):raise ValueError('全量预算清单已过期，未提交')
            self.guard(r,d,i);self.pending(db,r,d,i,row['id'])
            token=secrets.token_hex(24);d['permit']={'token':token,'consumed':False,'expires_at':d['command']['expires_at']}
            d.update(phase='verify',page=1,date_start=d['day'],command=None,begin_at=self.now(),submitted_document=v['document_id'],message='已发出一次网页修改许可，后续只回读原单',cycle=d.get('cycle',0)+1)
            db.execute("UPDATE api_actions SET state='submitting',updated_at=? WHERE id=?",(self.now(),row['id']));self.save(db,r,d)
        return {'ok':True,'submit_once':True,'submit_token':token}

    def final(self,p):
        if api_control(self.a):raise ValueError('暂停、预算、时长及ROI调控统一通过API，网页许可已关闭')
        if not isinstance(p,dict) or set(p)!={'run_id','action_id','submit_token'}:raise ValueError('最终许可字段无效')
        self.a.conflict_check()
        with self.a.gate,self.lock,self.a.db() as db:
            r=db.execute('SELECT * FROM hybrid_rule_runs WHERE id=?',(p['run_id'],)).fetchone()
            if not r or r['state'] in {'completed','cancelled'}:raise ValueError('轮次已结束')
            d=json.loads(r['data']);permit=d.get('permit') or {}
            if d.get('stop_requested') or p['action_id']!=d.get('action_id') or permit.get('consumed') is not False or not secrets.compare_digest(str(p['submit_token']),permit.get('token','')) or self.a.clock()>=parse_time(permit['expires_at']):raise ValueError('最终许可失效或已消费')
            i=json.loads(db.execute('SELECT intent FROM api_actions WHERE id=? AND state=\'submitting\'',(p['action_id'],)).fetchone()[0])
            self.guard(r,d,i);self.pending(db,r,d,i,p['action_id']);permit['consumed']=True
            db.execute("UPDATE api_actions SET state='unknown',updated_at=?,result=? WHERE id=? AND state='submitting'",(self.now(),json.dumps({'message':'网页一次点击许可已消费，等待独立回读；不会重发','ad_changes':0},ensure_ascii=False),p['action_id']))
            self.save(db,r,d)
        return {'ok':True,'allow':True}
