"""Integration of account-local cruise rules and explicitly approved actions."""
import copy
import threading

from .accounts import collection_policy, registry_accounts
from .cruise_actions import CruiseActions
from .cruise_runtime import CruiseRuntime
from .cruise_rules import FEATURE_CATALOG, FIELD_CATALOG
from .cost_guarantee import evaluate_cost_guarantee, SOURCES, RULE_VERSION
from .schema import parse_time, now_cn
from .plan_metrics import account_metrics, METRIC_CATALOG
from .batch_chase import BatchChaseActions, material_page_ready
from .live_close import LiveCloseSchedules
from .official_read_models import monitoring_model, official_account_active


class CruiseApp:
    def official_snapshots(self,aid):
        if not hasattr(self,'official_api') or aid not in self.official_api.plan_readings:return None
        from .official_workflow import task_snapshots
        with self.official_api.lock:
            return [s for w in self.official_api.workflows.values() if w['account_id']==aid for s in task_snapshots(w)]

    def official_scan(self,account,profile,lane):
        if not official_account_active(getattr(self,'official_api',None),account['id']):return None
        from .official_workflow import task_snapshots
        from .cruise_rules import evaluate_account
        reading=self.official_api.plan_readings.get(account['id'],{})
        parents=[p for p in reading.get('plans',[]) if p.get('opt_status')=='ENABLE' and p.get('status') not in {'DELETE','DELETED','FROZEN'}]
        result={'account_id':account['id'],'proposals':[],'skipped':[],'alerts':[],'diagnostics':[],
            'summary':'','mode':'per_action_confirmation','executed':False,'scanned':0,
            'material_batches':0,'materials_pending_confirmation':0,'material_pages_read':0,'material_rows_scanned':0,'invalid_material_id_rows':0}
        if not reading:
            poll=self.official_api.polling.get(account['id'],{})
            reason=poll.get('components',{}).get('plans',{}).get('error') or poll.get('error') or '等待首次完整读取计划列表'
            result['summary']='API计划读取未完成：'+reason+'；本轮没有可核验的计划，未生成调控候选。'
            result['diagnostics']=[{'feature_id':'account','status':'api_plans_pending','reason':reason}]
            return result
        if account['scene']!='live':
            result['summary']='商品计划数据已通过API监测；请在主计划页选择明确计划读取调控任务。'
            return result
        grant=next((g for g in self.official_actions.auto.status() if g['account_id']==account['id'] and g['enabled']),None) if hasattr(self,'official_actions') else None
        if lane=='auto' and grant:
            if grant.get('execution_mode')=='single_test':
                result.update(mode='single_test',summary='本户仅测试一条网页追投任务，完成后停止；连续自动追投关闭。')
                result['diagnostics']=[{'feature_id':'f5','status':'single_test','reason':grant['message']}]
                return result
            result.update(mode='automatic',summary='本户全自动追投已开启；独立执行器按整轮扫描、分批创建和已授权额度运行。'+grant['message'])
            result['diagnostics']=[{'feature_id':'f5','status':'api_auto_enabled','reason':grant['message']}]
            return result
        rule_state=next((g for g in self.official_actions.rule_auto.status() if g['account_id']==account['id']),None) if hasattr(self,'official_actions') else None
        rule_grant=rule_state if rule_state and rule_state['enabled'] else None
        if lane=='main' and rule_grant:
            result.update(mode='automatic',summary=rule_grant['message'])
            result['diagnostics']=rule_grant['diagnostics']
            return result
        if not parents:
            result['summary']='本户没有明确启用的API直播计划；等待下一次计划读取。';return result
        for p in parents:
            workflow=self.official_api.scan_workflow(account,profile,p['plan_id'],materials=lane=='auto',rule_metrics=lane=='main')
            if lane=='main':
                effective=copy.deepcopy(profile);effective['enabled']=True;effective['features']['f5']['enabled']=False
                history=self.cruise_actions.history(account['id'])+(self.official_actions.history(account['id']) if hasattr(self,'official_actions') else [])
                part=evaluate_account(account['id'],effective,task_snapshots(workflow),now=self.cruise.clock(),history=history)
                if profile['features']['f6']['enabled']:
                    part['diagnostics']=[d for d in part['diagnostics'] if d.get('feature_id')!='f6']
                    part['diagnostics'].append({'feature_id':'f6','status':'api_live_metrics_unavailable',
                        'reason':'主计划ROI的API确认修改已接通；当前已接入数据尚未取得同一直播间近15/30分钟ROI及实时在线人数，自动F6不会用全天ROI代替。可在主计划API调控中填写明确ROI值准备核对单。'})
                for k in ('proposals','skipped','alerts','diagnostics'):result[k].extend(part[k])
                result['scanned']+=part['scanned']
            else:
                result['scanned']+=len(workflow['materials'])
                ready=[b for b in workflow['batches'] if b['ready']]
                blocked=[b for b in workflow['batches'] if not b['ready']]
                result['material_batches']+=len(ready)
                result['materials_pending_confirmation']+=sum(len(b['material_ids']) for b in ready)
                result['material_pages_read']+=workflow.get('pages_read',0)
                result['material_rows_scanned']+=workflow.get('scanned_rows',len(workflow['materials']))
                result['invalid_material_id_rows']+=len(workflow.get('unresolved_material_rows',[]))
                result['diagnostics'].append({'feature_id':'f5','status':'api_batch_review' if ready else 'api_batch_blocked' if blocked else 'api_batch_empty',
                    'reason':f"计划{p['plan_id']}读取{workflow.get('pages_read',0)}页、{workflow.get('scanned_rows',len(workflow['materials']))}条原始记录，取得{len(workflow['materials'])}个有效素材ID；{len(ready)}批共{sum(len(b['material_ids']) for b in ready)}条待确认，{len(blocked)}批未通过创建检查。"+
                        ('消耗门槛以上范围已扫完。' if workflow.get('eligible_range_complete') else '范围未完整核验。')+
                        ('；'.join(dict.fromkeys(n for b in blocked for n in b.get('blocked_reasons',[]))) if blocked else '')})
        result['summary']=f"API本户扫描{result['scanned']}个任务/素材，生成{len(result['proposals'])}条规则候选；"+(
            '素材批次存在未通过的创建条件，请查看诊断；未提交投放修改。' if any(d['status']=='api_batch_blocked' for d in result['diagnostics']) else '素材批次在API追投区独立核对，未提交投放修改。')
        if lane=='main':
            stopped=rule_state and rule_state.get('state')=='blocked'
            reason=('六项自动执行已停止：'+rule_state['message']) if stopped else '当前只生成待确认候选；未获得本户六项自动执行授权。'
            result['summary']=f"API本户扫描{result['scanned']}个任务，生成{len(result['proposals'])}条规则候选；"+reason
            result['diagnostics'].append({'feature_id':'rule_auto','status':'api_rule_auto_stopped' if stopped else 'api_rule_review','reason':reason})
        if lane=='auto':result['summary']=f"API读取{result['material_pages_read']}页、{result['material_rows_scanned']}条原始记录，取得{result['scanned']}个有效素材ID，排除{result['invalid_material_id_rows']}条无效ID记录；命中{result['material_batches']}批共{result['materials_pending_confirmation']}条待确认，尚未提交投放。未启用本户F5独立预算和批数授权，不会自动新建追投。"
        return result

    def init_cruise(self):
        # Both queues share one process-wide critical section for claim/begin
        # and navigation. They retain separate task and parent-plan identities.
        self.action_claim_lock = threading.RLock()
        self.status_read_lock = threading.RLock()
        self.console_read_lock = threading.Lock()
        self.console_read = None
        self.cruise_actions = CruiseActions(self.root/'data/cruise-actions.db',
            action_validator=self.validate_cruise_action)
        self.cruise = CruiseRuntime(self.root/'data/cruise-runtime.db',
            # Settings are replaced atomically; avoid App.lock -> cruise.lock
            # versus cruise.lock -> App.lock inversion during ingestion/ticks.
            self.cruise_accounts, self.store.latest_observations,
            history_provider=self.cruise_actions.history)
        self.batch_chase = BatchChaseActions(self.root/'data/batch-chase.db',self.batch_profile,clock=lambda:self.cruise.clock())
        self.live_close = LiveCloseSchedules(self.root/'data/live-close.db',clock=lambda:self.cruise.clock())
        self.cruise.official_scanner=self.official_scan
        self.cruise.official_snapshot_provider=self.official_snapshots

    def batch_profile(self, aid):
        account=next((a for a in self.cruise.status()['accounts'] if a['id']==aid),None)
        if not account:raise ValueError('批量追投账户不存在')
        return account['profile'],account['revision']

    def cruise_accounts(self):
        settings=self.settings
        assessments={a['id']:a for a in settings['accounts']}
        return [{**a,'target_roi':assessments[a['id']].get('target_roi'),
            'roi_basis':assessments[a['id']].get('roi_basis','unknown')} for a in registry_accounts(settings)]

    def validate_cruise_action(self, action_id, intent):
        self.cruise.validate_action(action_id, intent)
        self.validate_cost_action(intent)

    def cost_review(self, intent):
        aid = intent['account_id']; is_task = intent.get('target_kind') == 'control_task'
        candidates = self.cruise._observations(aid) if is_task else self.store.latest_observations(aid)
        context = {'account_id':aid, 'plan_id':intent['plan_id']}
        for s in sorted(candidates, key=lambda row:parse_time(row['source']['captured_at']), reverse=True):
            if is_task:
                if s.get('scope') != 'control' or s.get('entity_id') != intent['task_id']: continue
                if (s.get('context',{}).get('parent_plan_id') or s.get('source',{}).get('parent_entity_id')) != intent['plan_id']:continue
            elif s.get('scope') != 'plan' or s.get('entity_id') != intent['plan_id']: continue
            context = {**copy.deepcopy(s), **context}
            break
        if is_task: context['task_id'] = intent['task_id']
        queue = self.cruise_actions if is_task else self.browser_actions
        history = queue.history(aid)
        return evaluate_cost_guarantee(context,intent,history,now=self.cruise.clock())

    def validate_cost_action(self, intent):
        if hasattr(self,'official_api') and intent.get('account_id') in self.official_api.plan_readings:
            raise ValueError('本户已接入API计划数据，不能沿用浏览器确认单；API确认执行尚未开启')
        review = self.cost_review(intent)
        if not review['allowed']:
            raise ValueError('；'.join(review['blocking_reasons']))

    def console_status(self, timeout=4):
        """Share one dashboard build and bound how long HTTP waits for it."""
        with self.console_read_lock:
            job = self.console_read
            if job is None:
                job = self.console_read = {'done': threading.Event()}
                threading.Thread(target=self._load_console_status, args=(job,),
                                 name='cruise-console-status', daemon=True).start()
        if not job['done'].wait(timeout):
            return None
        if 'error' in job:
            raise job['error']
        return job['value']

    def _load_console_status(self, job):
        try:
            job['value'] = self.cruise_status(console=True)
        except Exception as exc:
            job['error'] = exc
        finally:
            with self.console_read_lock:
                self.console_read = None
                job['done'].set()

    def cruise_status(self, *, console=False):
        # Dashboard snapshots never grant write permission. Do not wait behind
        # a platform preflight/network call holding the financial claim gate.
        # Mutations still validate their exact target under action_claim_lock.
        with self.status_read_lock:
            policy = collection_policy(self.cfg())
            value = self.cruise.status()
            main = self.browser_actions.status(policy)
            tasks = self.cruise_actions.status(policy)
            surfaces=[s for w in main['workers'] if w['current'] for s in w['surfaces']]
            monitoring=[account_metrics(a,self.store.latest_observations(a['id']),surfaces,self.cruise.clock())
                for a in value['accounts']]
            if hasattr(self,'official_api'):
                with self.official_api.lock:
                    monitoring=[monitoring_model(a,self.official_api.plan_readings.get(a['id'],{}),self.official_api.readings,self.cruise.clock())
                        if official_account_active(self.official_api,a['id']) else m for a,m in zip(value['accounts'],monitoring)]
                    value['api_polling']=copy.deepcopy(self.official_api.polling)
                    value['api_poll_interval_seconds']=self.official_api.poll_interval
                    if console:
                        value['api_workflows'] = {
                            key: {**{k:v for k,v in work.items() if k not in {'materials','unresolved_material_rows'}},
                                  'material_count':len(work.get('materials', [])),
                                  'unresolved_material_count':len(work.get('unresolved_material_rows', []))}
                            for key,work in self.official_api.workflows.items()
                        }
                    else:
                        value['api_workflows']=copy.deepcopy(self.official_api.workflows)
                    value['api_scan_progress']=copy.deepcopy(self.official_api.workflow_progress)
                value['api_rate_control']=self.official_api.rate_status()
                if hasattr(self,'official_actions'):
                    value['execution_strategy']=dict(self.official_actions.execution_strategy)
                    value['api_actions']=self.official_actions.status(console=console)
                    value['api_task_holds']=self.official_actions.holds()
                    value['api_followups']=self.official_actions.followups()
                    value['api_schedules']=self.official_actions.schedules.status()
                    value['api_auto']=self.official_actions.auto.status()
                    value['api_material_exclusions']=self.official_actions.exclusion_status()
                    value['api_material_deferrals']=self.official_actions.deferrals()
                    value['api_rule_auto']=self.official_actions.rule_auto.status()
                    value['api_budget_recovery']=self.official_actions.budget_recovery.status()
                    value['hybrid_chase']=self.official_actions.hybrid.status() if hasattr(self.official_actions,'hybrid') else []
                    value['ego_executor']=self.ego_executor.status() if hasattr(self,'ego_executor') else None
                    value['hybrid_rules']=self.official_actions.hybrid_rules.status() if hasattr(self.official_actions,'hybrid_rules') else []
            for queue in (main, tasks):
                for action in queue['actions']:
                    action['cost_guarantee'] = self.cost_review(action['intent'])
            value.update(feature_catalog=copy.deepcopy(FEATURE_CATALOG),
                field_catalog=copy.deepcopy(FIELD_CATALOG), browser_control=main,
                task_control=tasks, csrf=self.csrf, csrf_token=self.csrf,
                monitoring={'accounts':monitoring,'metric_catalog':METRIC_CATALOG},
                cost_guarantee_catalog={'version':RULE_VERSION,'sources':SOURCES,
                    'note':'成本保障按产品、工具和出价口径分别核算；页面状态与本地检查不代替平台赔付认定。'},
                now=self.cruise.clock().isoformat(),batch_control=self.batch_chase.status(policy),live_close=self.live_close.status(policy),
                execution_note='每条调控须单独确认，刷新后才核验设置。')
            return value

    def cruise_rule_draft(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'account_id', 'proposal_id'}:
            raise ValueError('需要明确账户与本轮巡航候选')
        proposal = self.cruise.proposal(payload['account_id'], payload['proposal_id'])
        if hasattr(self,'official_actions') and proposal['account_id'] in self.official_api.plan_readings:
            actions={'control_pause':'pause_task','control_budget_up':'task_budget','control_duration_up':'task_duration'}
            if proposal['action'] not in actions:raise ValueError('未知的API规则动作')
            request={'account_id':proposal['account_id'],'plan_id':proposal['parent_plan_id'],
                'action':actions[proposal['action']],'task_id':proposal['entity_id'],'reason':proposal['reason'][:500]}
            if proposal['action']!='control_pause':request['value']=float(proposal['after'])/(60 if proposal['action']=='control_duration_up' else 1)
            return self.official_actions.prepare(request,rule_check={k:proposal[k] for k in ('feature_id','entity_id','action','before','after')})
        if proposal.get('object_type') != 'control_task':
            raise ValueError('该候选尚无已接通的浏览器执行适配，不能准备调控单')
        policy = collection_policy(self.cfg())
        matches = [(w, s) for w in self.cruise_actions.status(policy)['workers'] if w['armed']
            for s in w['surfaces'] if s['account_id'] == proposal['account_id']
            and s['plan_id'] == proposal['parent_plan_id'] and s['task_id'] == proposal['entity_id']
            and proposal['action'] in s['actions']]
        if len(matches) != 1:
            raise ValueError('需有唯一、已开启的本户父计划和调控任务页面，请刷新本户任务观察')
        worker, surface = matches[0]
        draft = self.cruise_actions.create({'worker_id': worker['worker_id'], 'epoch': worker['epoch'],
            'account_id': surface['account_id'], 'plan_id': surface['plan_id'], 'task_id': surface['task_id'],
            'action': proposal['action'], 'after': proposal['after'], 'reason': proposal['reason'][:500]}, policy)
        try:
            self.cruise.bind_action(draft['id'], proposal)
        except Exception:
            self.cruise_actions.cancel({'id': draft['id']})
            raise
        draft['cost_guarantee'] = self.cost_review(draft['intent'])
        return draft

    def cruise_worker_jobs(self):
        with self.action_claim_lock:
            policy = collection_policy(self.cfg())
            allowed = not (self.browser_actions.has_pending() or self.cruise_actions.has_pending() or self.batch_chase.has_pending() or self.live_close.has_pending())
            result=self.cruise.worker_jobs(policy['revision'], allowed)
            if hasattr(self,'official_api'):
                result['jobs']=[j for j in result['jobs'] if j['account_id'] not in self.official_api.plan_readings]
            version=self.store.diagnostics().get('extension',{}).get('extension_version','0')
            try:loaded=tuple(int(n) for n in version.split('.'))
            except (AttributeError,ValueError):loaded=(0,)
            if loaded<(1,4,2):
                result['jobs']=[j for j in result['jobs'] if j.get('job_kind')!='plan_data']
                result['required_data_refresh_version']='1.4.2'
            if loaded<(1,5,0):
                result['jobs']=[j for j in result['jobs'] if j.get('job_kind')!='material_scan']
            if loaded<(1,6,4):
                result['jobs']=[j for j in result['jobs'] if j.get('job_kind')!='account_entry']
                result['required_account_entry_version']='1.6.4'
            return result

    def browser_action_call(self, name, payload):
        with self.action_claim_lock:
            if name in {'create','approve','create-from-recommendation','worker-claim','worker-begin'} and hasattr(self,'official_actions') and self.official_actions.has_pending():
                if name=='worker-claim':return {'ok':True,'command':None,'blocked':'先处理API确认单，防止两个执行器同时操作'}
                raise ValueError('先处理API确认单，防止两个执行器同时操作')
            policy = collection_policy(self.cfg())
            if name in {'worker-claim', 'worker-begin'} and (self.cruise_actions.has_active_or_unknown() or self.batch_chase.has_active_or_unknown() or self.live_close.has_pending()):
                if name == 'worker-claim':
                    return {'ok': True, 'command': None, 'blocked': '追投任务仍有执行中或结果不明的动作'}
                raise ValueError('追投任务仍有执行中或结果不明的动作')
            commands = {'create': self.browser_actions.create, 'approve': self.browser_actions.approve,
                'reconcile-preview': self.browser_actions.reconcile_preview,
                'reconcile-confirm': self.browser_actions.reconcile_confirm,
                'worker-observe': self.browser_actions.observe, 'worker-claim': self.browser_actions.claim,
                'worker-begin': self.browser_actions.begin}
            if name in commands:
                result = commands[name](payload, policy)
            elif name == 'create-from-recommendation':
                result = self.browser_recommendation_draft(payload)
            elif name == 'cancel': result = self.browser_actions.cancel(payload)
            elif name == 'worker-receipt': result = self.browser_actions.receipt(payload)
            else: raise ValueError('调控路径不存在')
            if name in {'create', 'create-from-recommendation'}:
                result['cost_guarantee'] = self.cost_review(result['intent'])
            return result

    def cruise_call(self, path, payload):
        if path=='/api/cruise/scan' and isinstance(payload,dict) and set(payload)=={'account_id','lane'} and hasattr(self,'official_api') and payload['account_id'] in self.official_api.plan_readings:
            return self.cruise.scan(payload['account_id'],payload['lane'],force_read=True)
        with self.action_claim_lock:
            name = path.rsplit('/', 1)[-1]
            if name in {'create','approve','worker-claim','worker-begin'} and hasattr(self,'official_actions') and self.official_actions.has_pending():
                if name=='worker-claim':return {'ok':True,'command':None,'blocked':'先处理API确认单，防止两个执行器同时操作'}
                raise ValueError('先处理API确认单，防止两个执行器同时操作')
            policy = collection_policy(self.cfg())
            if path.startswith('/api/cruise/close/'):
                if name=='worker-parents':
                    if not isinstance(payload,dict) or payload!={'revision':policy['revision']}:raise ValueError('读取范围已变化')
                    return {'ok':True,'parents':self.cruise.live_parent_mappings()}
                if name=='read-home':
                    if not isinstance(payload,dict) or set(payload)!={'account_id'}:raise ValueError('请指定直播账户')
                    return self.cruise.request_live_home(payload['account_id'])
                if name in {'worker-claim','worker-begin'} and self.live_close.has_pending() and (self.browser_actions.has_active_or_unknown() or self.cruise_actions.has_active_or_unknown() or self.batch_chase.has_active_or_unknown()):
                    if name=='worker-claim':return {'ok':True,'command':None,'blocked':'其他确认单或调控动作尚未处理'}
                    raise ValueError('其他确认单或调控动作尚未处理')
                if name in {'cancel','worker-receipt'}:return (self.live_close.cancel if name=='cancel' else self.live_close.receipt)(payload)
                methods={'create':self.live_close.create,'approve':self.live_close.approve,'worker-observe':self.live_close.observe,'worker-claim':self.live_close.claim,'worker-begin':self.live_close.begin}
                if name not in methods:raise ValueError('下播控量路径不存在')
                result=methods[name](payload,policy)
                if name=='worker-observe':self.cruise.observe_live_home(payload['surfaces'])
                return result
            if path.startswith('/api/cruise/batch/'):
                if name=='locate':
                    if not isinstance(payload,dict) or set(payload)!={'id'}:raise ValueError('需指定原批次')
                    old=next((r for r in self.batch_chase.status(policy)['actions'] if r['id']==payload['id']),None)
                    if not old or old['state'] not in ('draft','expired'):raise ValueError('仅定位尚未执行的原批次，结果不明批次须核验创建结果')
                    return self.cruise.locate_material_batch(old['intent'])
                if name in {'worker-claim','worker-begin'} and (self.browser_actions.has_active_or_unknown() or self.cruise_actions.has_active_or_unknown() or self.live_close.has_pending()):
                    if name=='worker-claim':return {'ok':True,'command':None,'blocked':'其他调控动作仍在执行或结果不明'}
                    raise ValueError('其他调控动作仍在执行或结果不明')
                if name in {'cancel','worker-receipt'}:return (self.batch_chase.cancel if name=='cancel' else self.batch_chase.receipt)(payload)
                methods={'create':self.batch_chase.create,'create-selected':self.batch_chase.create_selected,'renew':self.batch_chase.renew,'approve':self.batch_chase.approve,'worker-observe':self.batch_chase.observe,
                    'worker-claim':self.batch_chase.claim,'worker-begin':self.batch_chase.begin,'recheck':self.batch_chase.request_readback,
                    'worker-readback-jobs':self.batch_chase.readback_jobs,'worker-readback-result':self.batch_chase.readback_result}
                if name not in methods:raise ValueError('批量追投路径不存在')
                result=methods[name](payload,policy)
                if name=='worker-observe':
                    ready_surfaces=[s for s in payload['surfaces'] if material_page_ready(s)]
                    self.cruise.observe_material_pages(ready_surfaces)
                    if not (self.batch_chase.has_pending() or self.browser_actions.has_pending() or self.cruise_actions.has_pending() or self.live_close.has_pending()):
                        for preview in self.batch_chase.status(policy)['previews']:
                            if preview['batches']:continue
                            surface=next((s for s in ready_surfaces if s['account_id']==preview['account_id'] and s['plan_id']==preview['plan_id']),None)
                            if surface:self.cruise.next_material_page(surface)
                return result
            if path.startswith('/api/cruise/actions/'):
                if name in {'worker-claim', 'worker-begin'} and (self.browser_actions.has_active_or_unknown() or self.batch_chase.has_active_or_unknown() or self.live_close.has_pending()):
                    if name == 'worker-claim':
                        return {'ok': True, 'command': None, 'blocked': '主计划仍有执行中或结果不明的动作'}
                    raise ValueError('主计划仍有执行中或结果不明的动作')
                if name == 'prepare-rule': return self.cruise_rule_draft(payload)
                if name in {'cancel', 'worker-receipt'}:
                    return (self.cruise_actions.cancel if name == 'cancel' else self.cruise_actions.receipt)(payload)
                methods = {'create': self.cruise_actions.create, 'approve': self.cruise_actions.approve,
                    'reconcile-preview':self.cruise_actions.reconcile_preview,'reconcile-confirm':self.cruise_actions.reconcile_confirm,
                    'worker-observe': self.cruise_actions.observe, 'worker-claim': self.cruise_actions.claim,
                    'worker-begin': self.cruise_actions.begin}
                if name not in methods: raise ValueError('调控任务路径不存在')
                result = methods[name](payload, policy)
                if name == 'worker-observe': self.cruise.observe_tasks(payload['surfaces'])
                if name == 'create': result['cost_guarantee'] = self.cost_review(result['intent'])
                return result
            if name=='f5-batch-range':
                result=self.cruise.save_batch_range(payload)
                if hasattr(self,'official_actions'):
                    result['auto_authorizations_updated']=self.official_actions.refresh_automatic_profile(payload['account_id'])
                    self.official_actions.hybrid.stop({'account_id':payload['account_id']})
                    if hasattr(self.official_actions,'hybrid_rules'):self.official_actions.hybrid_rules.profile_changed(payload['account_id'])
                return result
            if name in {'save','save-auto'}:
                from .cruise_rules import profile_for_entry
                account=next((a for a in self.cruise_accounts() if a['id']==payload.get('account_id')),None)
                if account and 'profile' in payload:payload={**payload,'profile':profile_for_entry(payload['profile'],account)}
                result=self.cruise.save(payload)
                if name=='save-auto' and hasattr(self,'official_actions'):
                    result['auto_authorizations_updated']=self.official_actions.refresh_automatic_profile(payload['account_id'])
                return result
            if name == 'enter-account':
                if not isinstance(payload,dict) or set(payload)!={'account_id'}:raise ValueError('需指定账户ID')
                return self.cruise.request_account_entry(payload['account_id'])
            if name == 'refresh-data':
                if not isinstance(payload,dict) or set(payload)!={'account_id'}:raise ValueError('需明确刷新数据的账户')
                if hasattr(self,'official_api'):
                    account=next((a for a in self.cruise_accounts() if a['id']==payload['account_id']),None)
                    if account is None:raise ValueError('账户尚未登记')
                    gid=self.official_api.grant_for_account(account['id'])
                    if gid:
                        result=self.official_api.read_plans(account,gid)
                        self.official_api.read_account(account,gid)
                        return {'ok':True,'plan_count':len(result['result']['plans']),'ad_changes':0,
                            'note':'已通过官方API读取本户计划和今天报表，入口分别核对；未批准投放修改。'}
                return self.cruise.request_data(payload['account_id'])
            if name == 'control':
                if isinstance(payload,dict) and set(payload)=={'account_id','lane','running'} and payload.get('lane')=='auto' and payload.get('running') is False and hasattr(self,'official_actions'):
                    self.official_actions.auto.disable({'account_id':payload['account_id']})
                result = self.cruise.control(payload)
                if not payload['running']:
                    ids = self.cruise.rule_action_ids(payload['account_id'], payload['lane'])
                    for row in self.cruise_actions.status(policy)['actions']:
                        if row['id'] in ids and row['state'] in {'draft','approved'}:
                            self.cruise_actions.cancel({'id':row['id']})
                return result
            if name == 'scan':
                if not isinstance(payload, dict) or set(payload) != {'account_id', 'lane'}:
                    raise ValueError('请明确账户和巡航类型')
                return self.cruise.scan(payload['account_id'], payload['lane'],force_read=True)
            if name == 'worker-job-result': return self.cruise.job_result(payload)
            raise ValueError('巡航路径不存在')

    def cruise_loop(self):
        while not self.stop_event.is_set():
            try: self.cruise.tick()
            except Exception:
                # No advertisement operation originates in the scheduler.
                import logging
                logging.getLogger('qianchuan_monitor').exception('cruise scheduler failed')
            self.stop_event.wait(3)
