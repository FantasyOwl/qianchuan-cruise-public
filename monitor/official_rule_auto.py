"""Explicit per-account authority for task rules and main-plan ROI control.

F5 retains its separate monetary limits. Permission, an enabled rule, complete
official evidence and a fresh passing preflight are all required independently.
"""
import copy
from .execution_strategy import api_control
import json
import secrets
import threading
from datetime import timedelta
from decimal import Decimal
from .actuation import amount
from .cruise_rules import evaluate_account, _in_window
from .official_workflow import fresh_parent, task_snapshots, digest
from .schema import parse_time

FEATURES=tuple('f'+str(i) for i in range(1,7))
ACTION_MAP={'control_pause':'pause_task','control_budget_up':'task_budget','control_duration_up':'task_duration'}


class AutoRules:
    def __init__(self,actions):
        self.a=actions;self.lock=threading.Lock()
        self.lane_provider=lambda aid:True
        # A live adapter must supply verified same-room/window observations.
        # None is an explicit unavailable source, never today's ROI as 15m ROI.
        self.live_provider=lambda aid,pid:None
        with self.a.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS api_rule_auto(account_id TEXT PRIMARY KEY,plan_id TEXT,grant_id TEXT,profile_digest TEXT,profile_revision TEXT,features TEXT,enabled INTEGER,next_at TEXT,state TEXT,message TEXT,diagnostics TEXT)')

    def status(self):
        with self.a.db() as db:rows=[dict(r) for r in db.execute('SELECT * FROM api_rule_auto')]
        return [{**r,'features':json.loads(r['features']),'enabled':bool(r['enabled']),
            'diagnostics':json.loads(r['diagnostics'])} for r in rows]

    def row(self,aid):return next((r for r in self.status() if r['account_id']==aid),None)

    def configure(self,payload):
        fields={'account_id','plan_id','profile_revision','features','enabled','confirmed'}
        if not isinstance(payload,dict) or set(payload)!=fields or type(payload['enabled']) is not bool:raise ValueError('需要明确本户计划、六项授权范围和保存的规则版本')
        if not isinstance(payload['features'],list) or payload['features']!=list(FEATURES):raise ValueError('统一全自动授权范围须明确列出F1至F6')
        account=self.a.account(payload['account_id']);aid=account['id']
        if account['scene']!='live':raise ValueError('F1至F6是直播调控规则；图文户不能套用直播规则')
        profile,revision=self.a.profile(aid)
        if payload['profile_revision']!=revision:raise ValueError('规则已变化，请核对本户已保存的规则版本')
        p=fresh_parent(self.a.api,account,str(payload['plan_id']))
        if payload['enabled'] and payload['confirmed'] is not True:raise ValueError('需要明确授权按本户保存规则全自动执行')
        if payload['enabled'] and not profile['enabled']:raise ValueError('本户规则总开关尚未启用')
        with self.a.db() as db:
            # Authorization only. Pending human drafts remain untouched and
            # block the dispatcher; granting permission never submits them.
            db.execute('INSERT OR REPLACE INTO api_rule_auto VALUES(?,?,?,?,?,?,?,?,?,?,?)',(
                aid,p['plan_id'],secrets.token_hex(16),digest(profile),revision,json.dumps(list(FEATURES)),int(payload['enabled']),
                self.a.clock().isoformat(),'enabled' if payload['enabled'] else 'disabled',
                'F1至F6已获得全自动授权；仅执行已启用且数据和参数完整的规则。F5沿用独立预算与批数额度。' if payload['enabled'] else '六项统一授权已关闭。','[]'))
        self.a.auto.audit(aid,'rule_auto_authorized',{'features':list(FEATURES),'enabled':payload['enabled'],'plan_id':p['plan_id'],'ad_changes':0})
        return {'ok':True,'rule_auto':self.row(aid),'ad_changes':0}

    def disable(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'account_id'}:raise ValueError('需要明确关闭哪一户的六项全自动授权')
        self.a.account(payload['account_id'])
        with self.a.db() as db:db.execute("UPDATE api_rule_auto SET enabled=0,state='disabled',message='六项全自动授权已关闭，不再提交新调控。' WHERE account_id=?",(payload['account_id'],))
        self.a.auto.disable(payload)
        self.a.auto.audit(payload['account_id'],'rule_auto_disabled',{'ad_changes':0})
        return {'ok':True,'ad_changes':0}

    def finish(self,aid,state,message,diagnostics=None,*,disable=False,ad_changes=0):
        with self.a.db() as db:
            db.execute('UPDATE api_rule_auto SET state=?,message=?,diagnostics=?,enabled=CASE WHEN ? THEN 0 ELSE enabled END WHERE account_id=? AND (enabled=1 OR ?)',
                (state,message,json.dumps(diagnostics or [],ensure_ascii=False),disable,aid,disable))
        self.a.auto.audit(aid,'rule_auto_scan',{'state':state,'message':message,'diagnostics':diagnostics or [],'disabled':disable,'ad_changes':ad_changes})

    def verify(self,intent,tasks=None,*,browser_inventory=False):
        authority=intent.get('rule_auto_authority');row=self.row(intent['account_id'])
        fid=(intent.get('rule_check') or {}).get('feature_id')
        if fid not in {'f1','f2','f3','f4','f6'}:raise ValueError('素材追投须单独核对F5额度授权')
        if not authority or not row or not row['enabled'] or authority.get('grant_id')!=row['grant_id'] or row['plan_id']!=intent['plan_id'] or fid not in row['features']:
            raise ValueError('本户该项全自动授权已关闭或范围已变化，未提交调控')
        p,rev=self.a.profile(intent['account_id'])
        if rev!=row['profile_revision'] or digest(p)!=row['profile_digest']:raise ValueError('本户规则已变化，需要重新启用六项全自动授权')
        if not p['enabled'] or not p['features'][fid]['enabled'] or not self.lane_provider(intent['account_id']):raise ValueError('本户主巡航或该功能未开启，未提交调控')
        clock=self.a.clock().strftime('%H:%M');b=p['broadcast_protection']
        if not _in_window(clock,p['operation_start'],p['operation_end']) or b['enabled'] and _in_window(clock,b['start'],b['end']):raise ValueError('当前操作时段受限或停播保护生效')
        if fid=='f6' and intent['action']!='plan_roi' or fid in {'f1','f4'} and intent['action']!='pause_task' or fid=='f2' and intent['action']!='task_budget' or fid=='f3' and intent['action'] not in {'task_budget','task_duration'}:
            raise ValueError('功能授权与实际原生动作不一致')
        expected=intent['rule_check']
        if expected['entity_id']!=(intent['plan_id'] if fid=='f6' else intent.get('task_id')):raise ValueError('命中规则与实际操作对象不一致')
        if intent['action'] in {'task_budget','task_duration','plan_roi'}:
            after=amount(intent['after'])*(60 if intent['action']=='task_duration' else 1)
            if after!=amount(expected['after']):raise ValueError('原生目标值与命中规则的数值或单位不一致')
        if intent['action']=='task_budget':
            chase=self.a.auto.row(intent['account_id'])
            # Preserve an existing F5 cap when automatic task control grows
            # a chase created by this program. Check current native budgets.
            if chase and chase['budget_cap'] is not None and intent['task_id'] in self.a.auto.owned(intent['account_id'],intent['plan_id']):
                if tasks is None:raise ValueError('缺少当前在投任务，不能核对自动预算上限')
                self.a.auto.check_budget({'account_id':intent['account_id'],'plan_id':intent['plan_id'],
                    'body':{'budget':str(amount(intent['after'])-amount(intent['before']))}},tasks,chase['budget_cap'],**({'allow_lookup':False} if browser_inventory else {}))

    def saved_budget_target(self,intent):
        """Only this account's current explicit policy can override the old 50% cap."""
        if intent.get('action')!='task_budget':return False
        check=intent.get('rule_check') or {};fid=check.get('feature_id')
        if fid!='f2':return False
        row=self.row(intent['account_id']);authority=intent.get('rule_auto_authority') or {}
        if not row or not row['enabled'] or row['grant_id']!=authority.get('grant_id') or row['plan_id']!=intent['plan_id']:return False
        profile,revision=self.a.profile(intent['account_id']);cfg=profile['features'][fid]
        if revision!=row['profile_revision'] or digest(profile)!=row['profile_digest'] or not cfg['enabled'] or cfg.get('budget_policy')!='saved_target':return False
        if check.get('action')!='control_budget_up' or check.get('entity_id')!=intent.get('task_id') or amount(check['after'])!=amount(intent['after']):return False
        return any(rule.get('enabled',True) and rule.get('target_control_budget') is not None and amount(rule['target_control_budget'])==amount(intent['after']) for rule in cfg['rules'])

    def f6_candidate(self,aid,pid,profile,plan):
        cfg=profile['features']['f6']
        if not cfg['enabled']:return None,'功能未启用'
        fields=('min_overall_roi','max_online_users','min_15m_roi','min_30m_roi','target_roi_delta','min_interval_minutes')
        if any(cfg.get(k) is None for k in fields):return None,'F6阈值未填写完整'
        value=self.live_provider(aid,pid);now=self.a.clock()
        if not value:return None,'缺少同一直播间官方近15/30分钟ROI和实时在线人数'
        try:
            if value['account_id']!=aid or value['plan_id']!=pid or value['source']!='official_api' or value['metric_basis']!='overall_payment' or value['metric_date']!=now.date().isoformat():raise ValueError()
            captured=parse_time(value['captured_at'])
            if not 0<=(now-captured).total_seconds()<=profile['snapshot_max_age_seconds']:raise ValueError()
            for name,minutes in (('roi_15m',15),('roi_30m',30)):
                w=value['windows'][name];end=parse_time(w['end_at']);start=parse_time(w['start_at'])
                if abs((end-start).total_seconds()-minutes*60)>1 or abs((captured-end).total_seconds())>90:raise ValueError()
            m=value['metrics'];numbers={k:Decimal(str(m[k])) for k in ('roi','roi_15m','roi_30m','online_users')}
            if any(not v.is_finite() or v<0 for v in numbers.values()):raise ValueError()
        except (KeyError,ValueError,TypeError,ArithmeticError):return None,'F6官方数据身份、口径、窗口或新鲜度未通过核验'
        if not (numbers['roi']>Decimal(str(cfg['min_overall_roi'])) and numbers['roi_15m']>Decimal(str(cfg['min_15m_roi'])) and numbers['roi_30m']>Decimal(str(cfg['min_30m_roi'])) and numbers['online_users']<Decimal(str(cfg['max_online_users']))):return None,'F6条件尚未同时命中'
        for h in self.a.history(aid):
            if h['plan_id']!=pid or h['action'] not in {'roi_target_up','roi_target_down'} or h['state'] in {'draft','checking','cancelled','expired','rejected'}:continue
            if (now-parse_time(h['at'])).total_seconds()<max(60,cfg['min_interval_minutes'])*60:return None,'F6尚在主计划ROI调整冷却期'
        try:
            before=amount(plan.get('target_roi'));after=amount(before+Decimal(str(cfg['target_roi_delta'])))
            if after==before or abs((after-before)/before)>Decimal('.2'):raise ValueError()
        except ValueError:return None,'当前主计划ROI或调整幅度未通过核验'
        return {'feature_id':'f6','entity_id':pid,'action':'roi_target_up' if after>before else 'roi_target_down','before':str(before),'after':str(after)},None

    def check_f6(self,intent,plan):
        profile,_=self.a.profile(intent['account_id'])
        current,reason=self.f6_candidate(intent['account_id'],intent['plan_id'],profile,plan)
        if current!=intent['rule_check']:raise ValueError(reason or 'F6当前原生ROI或条件已变化，未执行旧目标')

    def candidates(self,account,profile,pid):
        workflow=self.a.api.scan_workflow(account,profile,pid,materials=False,rule_metrics=True)
        effective=copy.deepcopy(profile);effective['features']['f5']['enabled']=False;effective['features']['f6']['enabled']=False
        held={h['task_id'] for h in self.a.holds(account['id'],pid)}
        snapshots=[s for s in task_snapshots(workflow) if s['entity_id'] not in held]
        part=evaluate_account(account['id'],effective,snapshots,now=self.a.clock(),
            history=[h for h in self.a.history(account['id']) if h['state'] not in {'draft','checking'}])
        plan=fresh_parent(self.a.api,account,pid)
        f6,reason=self.f6_candidate(account['id'],pid,profile,plan)
        diagnostics=[]
        for fid in FEATURES:
            if fid=='f5':
                row=self.a.auto.row(account['id']);message='独立额度授权已开启，按素材周期执行' if row and row['enabled'] else '等待开启F5独立预算及批数授权'
            elif not profile['features'][fid]['enabled']:message='已授权，功能规则未启用'
            elif fid=='f6':message=reason or 'F6条件命中，可自动调整ROI'
            else:
                reasons=list(dict.fromkeys(n for skipped in part['skipped'] if skipped.get('feature_id')==fid for n in skipped.get('reasons',[])))
                message='；'.join(reasons[:8]) if reasons else '规则条件命中' if any(p['feature_id']==fid for p in part['proposals']) else '本轮没有命中条件的任务'
            diagnostics.append({'feature_id':fid,'authorized':True,'rule_enabled':profile['features'][fid]['enabled'],'reason':message})
        return part['proposals']+([f6] if f6 else []),diagnostics

    def tick(self):
        if not self.lock.acquire(blocking=False):return
        try:
            for row in self.status():
                if not row['enabled'] or parse_time(row['next_at'])>self.a.clock():continue
                aid=row['account_id'];profile,rev=self.a.profile(aid)
                if rev!=row['profile_revision'] or digest(profile)!=row['profile_digest']:
                    self.finish(aid,'blocked','规则已变化，六项自动执行已停止；核对后重新启用授权。',disable=True);continue
                if not self.a.lock.acquire(blocking=False):continue
                rid=None;diagnostics=[];completed=0;interval=profile['main_interval_seconds']
                try:
                    bridge=None if api_control(self.a) else getattr(self.a,'hybrid_rules',None)
                    if bridge and bridge.active(aid):
                        self.finish(aid,'waiting_browser','本户F1–F4/F6网页补位进行中；等待原单核验，不并行发送API动作。');continue
                    if not self.lane_provider(aid):
                        self.finish(aid,'idle','六项已授权，等待开启本户主巡航。');continue
                    clock=self.a.clock().strftime('%H:%M');protection=profile['broadcast_protection']
                    if not profile['enabled']:
                        self.finish(aid,'idle','本户规则总开关已关闭，自动调控等待开启。');continue
                    if not _in_window(clock,profile['operation_start'],profile['operation_end']):
                        self.finish(aid,'waiting_window','当前不在本户操作时段 '+profile['operation_start']+'—'+profile['operation_end']+'，时段内恢复。已确认的下播排程独立执行。');continue
                    if protection['enabled'] and _in_window(clock,protection['start'],protection['end']):
                        self.finish(aid,'waiting_window','当前处于本户停播保护时段，自动调控等待恢复。');continue
                    account=self.a.account(aid)
                    if bridge and self.a.hybrid.mode(aid)=='browser_only':
                        bridge.request(aid,reason='本户选择仅网页调控')
                        self.finish(aid,'waiting_browser','已安排网页按本户F1–F4/F6规则全量检查。');continue
                    pending=[r for r in self.a.status(pending_only=True)
                        if r['intent']['account_id']==aid and r['intent']['plan_id']==row['plan_id']]
                    for old in pending:
                        if self.a.held(old['intent']):continue
                        same_grant=(old['intent'].get('rule_auto_authority') or {}).get('grant_id')==row['grant_id']
                        if old['state']=='draft' and same_grant:self.a.cancel({'id':old['id']})
                        elif old['readback_available']:
                            self.a.recheck({'id':old['id']})
                    with self.a.db() as db:self.a._pending(db,{'account_id':aid,'plan_id':row['plan_id'],'action':'auto_rule_scan'})
                    candidates,diagnostics=self.candidates(account,profile,row['plan_id'])
                    missing_native_fields=any(d.get('feature_id') in {'f1','f2','f3','f4'} and d.get('rule_enabled') and
                        '缺少明确调控综合成本' in d.get('reason','') for d in diagnostics)
                    if missing_native_fields and bridge and self.a.hybrid.mode(aid)!='api_only':
                        requested=bridge.request(aid,reason='API未提供已启用规则所需的调控综合成本，转网页读取同一口径')
                        interval=min(interval,15)
                        self.finish(aid,'waiting_browser','API缺少调控综合成本，已安排网页按本户原口径检查。' if requested else '等待当前网页阶段结束后补读调控综合成本；不以消耗替代。',diagnostics)
                        continue
                    if candidates and not api_control(self.a) and hasattr(self.a,'hybrid') and self.a.hybrid.read_only_active(aid) and not self.a.hybrid.yield_readonly(aid):
                        interval=min(interval,15)
                        self.finish(aid,'waiting','F5当前读取阶段仍在回传，保留本次读取；阶段结束后优先重查规则。');continue
                    for proposal in candidates[:profile['safety']['max_actions_per_round']]:
                        self.a.schedules.tick();self.a.tick_followups()
                        fid=proposal['feature_id'];expected={k:proposal[k] for k in ('feature_id','entity_id','action','before','after')}
                        if fid not in row['features']:continue
                        # Pauses have no before-budget/duration dependency;
                        # confirm performs an exact fresh task read and rule
                        # evaluation. Avoid a second whole-plan scan here.
                        if fid not in {'f1','f4'}:
                            self.a.api.scan_workflow(account,profile,row['plan_id'],materials=False)
                        request={'account_id':aid,'plan_id':row['plan_id'],'reason':'全自动命中本户'+fid.upper()+'已保存规则'}
                        if fid=='f4' and profile['features']['f4']['pause_rule']=='daily_spend_roi':request['reason']=proposal['reason']
                        if fid=='f6':request.update(action='plan_roi',value=proposal['after'])
                        else:
                            request.update(action=ACTION_MAP[proposal['action']],task_id=proposal['entity_id'])
                            if proposal['action']!='control_pause':request['value']=str(Decimal(str(proposal['after']))/(60 if proposal['action']=='control_duration_up' else 1))
                        try:
                            record=self.a.prepare(request,rule_check=expected,rule_auto_authority={'grant_id':row['grant_id']})
                            rid=record['id'];out=self.a.confirm({'id':rid,'intent_digest':record['intent_digest'],'confirmed':True})
                        except ValueError as exc:
                            if rid:
                                saved=next((r for r in self.a.status() if r['id']==rid),None)
                                if saved and saved['state']=='draft':self.a.cancel({'id':rid})
                            diagnostics.append({'feature_id':fid,'reason':'未提交：'+str(exc)});rid=None
                            if getattr(exc,'reason',None) in {'rate_limited','service_busy','quota_evidence_missing'} or str(getattr(exc,'origin','')).startswith('local_'):
                                interval=max(interval,getattr(exc,'retry_after',0))
                                if bridge and getattr(exc,'reason',None) in {'rate_limited','quota_evidence_missing'}:bridge.request(aid,reason='API提交前限流或任务额度证据不足，未提交本动作')
                                self.finish(aid,'waiting',f'本轮已完成{completed}条调控；'+str(exc),diagnostics,ad_changes=completed);break
                            continue
                        if out['state']!='verified':
                            result=out['result'] or {}
                            interval=max(interval,result.get('retry_after_seconds') or 0)
                            if out['state']=='draft':self.a.cancel({'id':out['id']})
                            if bridge and out['state'] in {'rejected','draft'} and (result.get('reason')=='rate_limited' or result.get('native_code')==40100) and result.get('submission_uncertain') is False:
                                bridge.request(aid,reason='平台明确返回40100拒绝，改用网页重新读数和判断规则')
                            temporary=result.get('retryable') or out['state']=='submitted_pending_verification'
                            state='waiting_verification' if out['state'] in {'unknown','submitted_pending_verification'} and temporary else 'waiting' if temporary else 'blocked'
                            self.finish(aid,state,f'本轮已完成{completed}条调控；'+result.get('message','本单等待核验'),diagnostics,disable=not temporary,ad_changes=completed);break
                        completed+=1;rid=None
                    else:self.finish(aid,'running' if completed else 'watching',f'六项全自动授权生效，本轮完成{completed}条调控；缺值或未启用的功能不执行。',diagnostics,ad_changes=completed)
                except Exception as exc:
                    if rid:
                        saved=next((r for r in self.a.status() if r['id']==rid),None)
                        if saved and saved['state']=='draft':self.a.cancel({'id':rid})
                    if bridge and getattr(exc,'reason',None) in {'rate_limited','quota_evidence_missing'}:
                        try:
                            if not bridge.request(aid,reason='API读取限流；尚未发送本轮调控'):interval=min(interval,15)
                        except ValueError:pass
                    interval=max(interval,getattr(exc,'retry_after',0))
                    self.finish(aid,'waiting',f'本轮已完成{completed}条调控；'+str(exc)[:300],diagnostics,ad_changes=completed)
                finally:
                    with self.a.db() as db:db.execute('UPDATE api_rule_auto SET next_at=? WHERE account_id=? AND grant_id=?',((self.a.clock()+timedelta(seconds=interval)).isoformat(),aid,row['grant_id']))
                    self.a.lock.release()
        finally:self.lock.release()
