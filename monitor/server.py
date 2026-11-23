from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
import time
import webbrowser
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .ai import make_package, request_analysis, validate_ai_config
from .actuation import prepare_action
from .catalog import coverage_report
from .demo import scenario,outcome_scenario,migration_scenario,emergency_scenario,quality_scenario,maintenance_scenario,roi_tuning_scenario,cold_start_scenario,roi_recovery_scenario,live_exclusion_scenario,roi_recovery_cross_day_scenario
from .evaluation import evaluate_all
from .events import normalize_event
from .migration import create_migration, ACTIVE_STATES
from .reporting import build_report, report_markdown
from .official_reports import convert_official_reports,official_evidence,reject_duplicate_keys
from .custom_reports import review_custom_reports
from .creative_rotation import preview_creative_rotations
from .creative_workflow import WorkflowLedger,qualified_draft
from .creative_workflow_demo import creative_workflow_replay
from .material_readback import review_supplied_materials
from .control_creation_readback import review_supplied_creation
from .creation_readback_demo import creation_readback_example
from .deadline_demo import deadline_example
from .material_chase_creation import preview_material_chase
from .material_chase_demo import chase_creation_scenario
from .smart_control_demo import smart_control_scenario, smart_control_preview
from .legacy import inspect_legacy
from .health import runtime_health
from .demo import product_carrying_scenario
from .demo import creative_idle_scenario
from .demo import creative_rotation_scenario
from .demo import control_creation_scenario
from .schema import normalize_snapshot, now_cn, validate_settings
from .store import Store
from .supervisor import supervision_status
from .parent_guard import parent_guard_status
from .accounts import collection_policy, registry_view, replace_registry, validate_collection_batch, LEGACY_SCOPE, MAX_ACCOUNTS

CLOSE_WORKER_PATHS={'/api/cruise/close/'+name for name in ('worker-observe','worker-parents','worker-claim','worker-begin','worker-receipt')}
HYBRID_WORKER_PATHS={'/api/cruise/hybrid/'+name for name in ('worker-next','worker-result','worker-begin','worker-final','worker-ping')}
RULE_WORKER_PATHS={'/api/cruise/hybrid-rules/worker-'+name for name in ('next','result','begin','final')}
HYBRID_WORKER_PATHS |= RULE_WORKER_PATHS
from .browser_actions import BrowserActions, ACTIONS as BROWSER_ACTIONS, recommendation_binding
from .cruise_api import CruiseApp
from .official_api import OfficialAPI
from .official_actions import OfficialActions
from .branding import BrandingStore
from .runtime_files import read_asset

ROOT = Path(__file__).resolve().parent.parent
LOG = logging.getLogger("qianchuan_monitor")


class App(CruiseApp):
    def __init__(self, root=ROOT, port=8877, *, release=None):
        self.root = Path(root)
        self.port = port
        self.release = release
        self.lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self.ai_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.config_path = self.root / "config.json"
        self.settings = json.loads(self.config_path.read_text("utf-8"))
        self.branding = BrandingStore(self.root/'data/ui-branding.json')
        self.store = Store(self.root/"data"/"monitor.db")
        self.browser_actions = BrowserActions(self.root/"data"/"browser-actions.db", recommendation_validator=self.validate_browser_recommendation,
            action_validator=self.validate_cost_action)
        self.init_cruise()
        token_file = self.root/"data"/"pairing-token.txt"
        if not token_file.exists():
            token_file.write_text(secrets.token_urlsafe(32), "utf-8")
        self.token = token_file.read_text("utf-8").strip()
        self.csrf = secrets.token_urlsafe(24)
        self.official_api = OfficialAPI(self.root/'data')
        self.official_actions = OfficialActions(self.root/'data/official-api-actions.db',self.official_api,self.cruise_accounts,self.batch_profile,
            gate=self.action_claim_lock,conflict_check=self.api_queue_conflicts)
        if self.release:
            self.official_api.transport.check_authorized = self.release.check
            self.official_actions.writer.check_authorized = self.release.check
        self.official_actions.auto.audit_writer=self.record_auto_event
        self.official_actions.rule_auto.lane_provider=lambda aid:next((a['lanes']['main']['running'] for a in self.cruise.status()['accounts'] if a['id']==aid),False)
        from .hybrid_chase import HybridChase
        self.official_actions.hybrid=HybridChase(self.official_actions,lambda:collection_policy(self.cfg()))
        from .hybrid_rules import HybridRules
        self.official_actions.hybrid_rules=HybridRules(self.official_actions)
        from .ego_executor import EgoExecutor
        self.ego_executor=EgoExecutor(self)
        self.official_actions.browser_executor=self.ego_executor
        self.started_at = now_cn().isoformat()
        self.run_id = 'run_' + secrets.token_hex(12)
        self.last_ai_at = 0.0
        self.last_eval_digest = ""
        self.store.audit("monitor_started", {"mode": "read_only", "executor": "awaiting_browser_worker", "port": port})

    def cfg(self):
        with self.lock:
            return copy.deepcopy(self.settings)

    def api_queue_conflicts(self):
        policy=collection_policy(self.cfg())
        queues=(self.browser_actions.status(policy),self.cruise_actions.status(policy),self.batch_chase.status(policy),self.live_close.status(policy))
        if any(r.get('state') in {'draft','approved','claimed','submitting','unknown','submitted_pending_verification'} for q in queues for r in q.get('actions',q.get('schedules',[]))):
            raise ValueError('先处理原浏览器队列中的待确认或结果不明动作，避免两个执行器同时操作')

    def record_auto_event(self,aid,event,detail):
        with self.cruise._tx() as db:self.cruise._log(db,aid,event,detail)

    def validate_browser_recommendation(self, expected):
        _, evaluation = self.current()
        proposal = next((r for r in evaluation['recommendations'] if r['id'] == expected.get('id')), None)
        if proposal is None or proposal.get('eligible') is not True or recommendation_binding(proposal) != expected:
            raise ValueError('原规则建议已变化或资格已失效，请重新分析')

    def browser_recommendation_draft(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'recommendation_id', 'worker_id', 'epoch'}:
            raise ValueError('需要当前规则建议及明确浏览器执行会话')
        _, evaluation = self.current()
        proposal = next((r for r in evaluation['recommendations'] if r['id'] == payload['recommendation_id']), None)
        if (proposal is None or proposal.get('eligible') is not True or proposal.get('scope') != 'plan'
                or proposal.get('action') not in BROWSER_ACTIONS):
            raise ValueError('当前建议未满足规则条件或尚无主计划浏览器适配')
        value = proposal['after'] if type(proposal['after']) is bool else str(proposal['after'])
        draft = {'worker_id': payload['worker_id'], 'epoch': payload['epoch'], 'account_id': proposal['account_id'],
                 'plan_id': proposal['entity_id'], 'action': proposal['action'], 'after': value, 'reason': proposal['reason']}
        return self.browser_actions.create(draft, collection_policy(self.cfg()), recommendation=proposal)

    def refresh_legacy(self):
        if not self.refresh_lock.acquire(blocking=False):
            return {"busy": True}
        try:
            snapshots, info = inspect_legacy(self.cfg())
            added = self.store.add(snapshots)
            self.store.diagnostic("legacy", info)
            if added:
                self.store.audit("legacy_read", {"added": added, "original_modified": False})
            return {"added": added, "issues": info["issues"]}
        finally:
            self.refresh_lock.release()

    def current(self,*,cfg=None,at=None):
        at = now_cn() if at is None else at
        cfg = self.cfg() if cfg is None else cfg
        allowed={a['id'] for a in cfg['accounts']}
        snapshots = [r for r in self.store.evaluation_snapshots(now=at) if r['account_id'] in allowed]
        events = [r for r in self.store.events(now=at) if r['account_id'] in allowed]
        recovery_snapshots, recovery_events = self.store.recovery_history(snapshots, events)
        migrations=[m for m in self.store.migrations() if m['account_id'] in allowed]
        evaluation = evaluate_all(snapshots, cfg, at,events=events, migrations=migrations,
            recovery_snapshots=recovery_snapshots, recovery_events=recovery_events)
        for migration in evaluation['budget_migrations']:
            if migration['state'] == 'completed_reported' and not migration.get('historical'):
                self.store.finalize_migration(migration)
        digest = hashlib.sha256(json.dumps(evaluation.get("recommendations", []), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with self.lock:
            if digest != self.last_eval_digest:
                self.store.audit("recommendations_evaluated", {"mode": "simulation_only", "recommendations": evaluation.get("recommendations", []), "executed": False})
                self.last_eval_digest = digest
        return snapshots, evaluation

    def monitor_runtime(self):
        value=runtime_health(self.store.diagnostics(),self.store.cycles(),self.run_id,
            self.started_at,max(30,self.cfg().get('poll_seconds',60)),now_cn())
        value['supervision']=supervision_status(self.root,os.getpid())
        value['parent_guard']=parent_guard_status()
        return value

    def creative_workflows(self):
        allowed={a['id'] for a in self.cfg()['accounts'] if a.get('observe',True)}
        records=WorkflowLedger.existing(self.root/'data/creative-workflows.db',with_journal=True)
        return {'ok':True,'workflows':[w for w in records if w['account_id'] in allowed],
            'native_material_submission_enabled':False,'real_ad_actions':0,'local_only':True}

    def create_creative_workflow(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'document_text','account_id','plan_id','intent_digest'}:
            raise ValueError('保存流程需提供登记文本、明确账户/计划及本次意图摘要')
        cfg=self.cfg();at=now_cn();snapshots,evaluation=self.current(cfg=cfg,at=at)
        preview=preview_creative_rotations({'document_text':payload['document_text']},snapshots,cfg,evaluation,at)
        draft=qualified_draft(preview,payload['account_id'],payload['plan_id'],payload['intent_digest'])
        if draft['synthetic_only']:raise ValueError('合成流程只在隔离回放运行，不保存到真实观察目录')
        ledger=WorkflowLedger(self.root/'data/creative-workflows.db')
        record=ledger.create(preview,payload['account_id'],payload['plan_id'],payload['intent_digest'],at)
        self.store.audit('creative_workflow_saved',{'workflow_id':record['id'],'executed':False,'native_adapter_available':False})
        return {'ok':True,'workflow':record,'local_only':True,'executed':False}

    def cancel_creative_workflow(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'id'} or not isinstance(payload['id'],str):
            raise ValueError('取消流程需明确本地流程ID')
        if not any(w['id']==payload['id'] for w in self.creative_workflows()['workflows']):
            raise ValueError('找不到当前观察账户的本地流程')
        record=WorkflowLedger(self.root/'data/creative-workflows.db').cancel_before_add(payload['id'],now_cn())
        self.store.audit('creative_workflow_cancelled_before_add',{'workflow_id':record['id'],'executed':False})
        return {'ok':True,'workflow':record,'local_only':True,'executed':False}

    def status(self):
        snapshots, evaluation = self.current()
        latest = {}
        for s in snapshots:
            key = (s["account_id"], s["scope"], s["entity_id"], s["metric_basis"], s["source"]["series_key"])
            if key not in latest or s["source"]["captured_at"] > latest[key]["source"]["captured_at"]:
                latest[key] = s
        browser_control = self.browser_actions.status(collection_policy(self.cfg()))
        if browser_control['execution_available']:
            # The pure rule engine remains an offline evaluator. Its legacy
            # runtime sentence must not contradict a connected approval worker.
            evaluation = {**evaluation, 'summary': evaluation['summary'].replace('真实执行不可用。', '').rstrip()
                          + ' 浏览器逐条确认执行已连接；每条投放修改仍须人工确认。'}
        return {"ok": True, "mode": "per_action_confirmation" if browser_control['execution_available'] else "read_only",
                "execution_available": browser_control['execution_available'], "browser_control": browser_control, "started_at": self.started_at,
                "now": now_cn().isoformat(), "settings": self.cfg(), "evaluation": evaluation,
                "latest": [{**s,'official_report_evidence':official_evidence(s)} for s in latest.values()], "diagnostics": self.store.diagnostics(),
                "metric_coverage": coverage_report(snapshots, self.cfg(), analysis=evaluation.get('monitoring_analysis',{})),
                "monitor_health": self.monitor_runtime(),
                "snapshot_count": len(snapshots), "csrf": self.csrf,
                "ai_key_present": bool(os.environ.get("QIANCHUAN_AI_API_KEY"))}

    def report(self, account_id):
        snapshots, evaluation = self.current()
        report = build_report(snapshots, self.cfg(), evaluation, account_id, now_cn())
        report['evidence_and_risks']['monitor_runtime'] = self.monitor_runtime()
        # Regenerate the report identity after adding runtime evidence.
        report.pop('id', None)
        report['id'] = hashlib.sha256(json.dumps(report, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()[:24]
        return report

    def ingest(self, payload, channel="import"):
        with self.lock:
            return self._ingest_locked(payload,channel)

    def _ingest_locked(self, payload, channel):
        if not isinstance(payload, dict) or not isinstance(payload.get("snapshots"), list):
            raise ValueError("请提供包含 snapshots 数组的 JSON 对象")
        if "events" in payload:
            raise ValueError("请将快照和操作报告分别导入，避免混淆观察数据与已报告动作")
        rows = payload["snapshots"]
        if not rows or len(rows) > 1000:
            raise ValueError("每次导入 1 到 1000 条快照")
        cfg = self.cfg()
        if channel=='extension':validate_collection_batch(cfg,payload)
        normalized = []
        import_batch = "batch_" + secrets.token_hex(12)
        for row in rows:
            row = copy.deepcopy(row)
            if channel in {"extension", "codex_plugin"}:
                row.setdefault("source", {})["kind"] = "browser_dom"
                row["source"]["dataset_id"] = channel+"-visible-summary-v2"
                observed_control = row.get("control", {})
                if not isinstance(observed_control, dict):
                    raise ValueError("浏览器对象状态无效")
                row["control"] = {**observed_control, "history_known": False, "guarantee_verified": False}
            elif row.get("source", {}).get("kind") in {"demo", "legacy_db", "browser_dom"}:
                # A manual import is never mistaken for a directly observed source.
                row["source"]["kind"] = "import"
            if channel == "import":
                row.setdefault("source", {}).setdefault("dataset_id", import_batch)
            normalized.append(normalize_snapshot(row, cfg))
        added = self.store.add(normalized)
        self.cruise.observations_arrived(normalized)
        self.store.audit("snapshots_ingested", {"channel": channel, "received": len(rows), "added": added, "executed": False})
        if channel in {"extension", "codex_plugin"}:
            self.store.patch_diagnostic("extension", {"state": "connected", "last_ingest_at": now_cn().isoformat(),
                                                "collector": channel, "received": len(rows), "coverage": "配置账户指定分组内的可见汇总"})
        return {"ok": True, "added": added, "received": len(rows), "mode": "read_only"}

    def official_report(self,payload,*,persist=False):
        result=convert_official_reports(payload,self.cfg())
        if persist:
            digest=payload.get('preview_digest')
            if (not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest)
                    or not hmac.compare_digest(digest,result['preview_digest'])):
                raise ValueError('请先预览当前报告；查询、数值或时间修改后须重新预览')
            added=self.store.add(result['snapshots'])
            self.store.audit('official_report_imported',{'received':result['received_reports'],'added':added,'executed':False,
                'scope':'declared_account_query','raw_response_saved':False,'credentials_used':False})
            return {'ok':True,'added':added,'received':result['received_reports'],'mode':'read_only','executed':False,'raw_response_saved':False}
        return {'ok':True,**result,'persisted':False,'mode':'read_only','raw_response_saved':False}

    def import_events(self,payload):
        if not isinstance(payload,dict) or "snapshots" in payload or not isinstance(payload.get("events"),list) or not 1<=len(payload["events"])<=1000:
            raise ValueError("请单独提供包含1到1000条events的操作报告文件")
        events=[normalize_event(row,self.cfg()) for row in payload["events"]]
        added=self.store.add_events(events)
        self.store.audit("operation_reports_imported",{"received":len(events),"added":added,"executed":False,
                            "note":"导入的是人工观察或模拟报告，不是平台执行回执"})
        return {"ok":True,"added":added,"received":len(events),"executed":False}

    def migration_plan(self, payload):
        if not isinstance(payload, dict) or any(not isinstance(payload.get(k), str) for k in ("source_snapshot_id", "target_snapshot_id")):
            raise ValueError("请明确选择旧计划和新计划的本地快照")
        with self.lock:
            snapshots, evaluation = self.current()
            plan = create_migration(payload["source_snapshot_id"], payload["target_snapshot_id"],
                                    snapshots, self.cfg(), evaluation, now_cn())
            if plan["state"] != "blocked":
                wanted = {plan[leg]["entity_id"] for leg in ("source", "target")}
                if any(p["account_id"] == plan["account_id"] and p["state"] in ACTIVE_STATES
                       and wanted.intersection(p[leg]["entity_id"] for leg in ("source", "target"))
                       for p in evaluation["budget_migrations"]):
                    raise ValueError("计划已参与未结束的本地预算交接，请先复核或取消")
                self.store.add_migration(plan)
                self.store.audit("budget_migration_planned", {"id": plan["id"], "amount": plan["amount"], "executed": False})
        return {"ok": True, "plan": plan, "persisted": plan["state"] != "blocked", "executed": False}

    def save_settings(self, value):
        validated = validate_settings(value)
        with self.lock:
            cfg = copy.deepcopy(self.settings)
            cfg["rules"].update(validated["rules"])
            supplied = {a["id"]: a for a in validated["accounts"]}
            if set(supplied) - {a["id"] for a in cfg["accounts"]}:
                raise ValueError("不能通过网页扩大账户白名单")
            for a in cfg["accounts"]:
                if a["id"] in supplied:
                    a["target_roi"] = supplied[a["id"]].get("target_roi")
                    a["roi_basis"] = supplied[a["id"]].get("roi_basis", "unknown")
            if "ai" in value:
                cfg["ai"] = validate_ai_config(value["ai"])
            # Only our new config is changed. Atomic replacement protects against torn writes.
            temp = self.config_path.with_suffix(".tmp")
            temp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
            os.replace(temp, self.config_path)
            self.settings = cfg
        self.store.audit("local_settings_saved", {"accounts": [{"id": a["id"], "target_roi": a["target_roi"], "roi_basis": a["roi_basis"]} for a in cfg["accounts"]],
                                                  "rules": cfg["rules"], "ai_enabled": cfg["ai"]["enabled"], "account_modified": False})
        return {"ok": True}

    def account_registry(self):
        return registry_view(self.cfg(),self.store.diagnostics().get('extension'),api_connected=self.official_api.connected())

    def save_account_registry(self,payload):
        with self.lock:
            previous={a['id']:a for a in self.settings['accounts']}
            cfg=replace_registry(self.settings,payload,self.store.diagnostics().get('extension'),api_connected=self.official_api.connected())
            temp=self.config_path.with_suffix('.tmp')
            temp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),'utf-8');os.replace(temp,self.config_path)
            self.settings=cfg
            with self.official_api.lock:
                for account in cfg['accounts']:
                    if any(previous.get(account['id'],{}).get(k)!=account.get(k) for k in ('scene','delivery_entry','observe')):
                        self.official_api.polling.pop(account['id'],None)
        self.store.audit('local_account_registry_saved',{'accounts':registry_view(cfg)['accounts'],'account_modified':False,'execution_available':False})
        return {'ok':True,'account_modified':False,'registry':self.account_registry()}

    def heartbeat(self,payload):
        if not isinstance(payload,dict):raise ValueError('采集状态无效')
        policy=collection_policy(self.cfg());protocol=payload.get('collector_protocol',1)
        if type(protocol) is not int or protocol not in {1,2}:raise ValueError('采集协议无效')
        if protocol==2 and payload.get('policy_revision')!=policy['revision']:raise ValueError('采集配置已变化，请重新读取配置')
        version=payload.get('extension_version')
        if version is not None and (not isinstance(version,str) or not re.fullmatch(r'\d{1,5}(?:\.\d{1,5}){1,3}',version)):
            raise ValueError('扩展版本声明无效')
        control_protocol=payload.get('browser_control_protocol')
        if control_protocol is not None and (type(control_protocol) is not int or control_protocol!=1):raise ValueError('执行桥协议声明无效')
        issues=payload.get('issues',[])
        if not isinstance(issues,list):raise ValueError('采集问题列表无效')
        if type(payload.get('tab_count',0)) is not int:raise ValueError('采集标签数量无效')
        issues=[str(x)[:200] for x in issues[:10]]
        if protocol==1 and policy['accounts']!=LEGACY_SCOPE:issues.append('旧采集器不支持当前账户配置，请重新加载新版扩展')
        state={'state':'connected','last_seen':now_cn().isoformat(),
            'tab_count':max(0,min(MAX_ACCOUNTS*2,int(payload.get('tab_count',0)))),
            'collector':'codex_plugin' if payload.get('collector')=='codex_plugin' else 'extension',
            'collector_protocol':protocol,'policy_revision':policy['revision'] if protocol==2 else None,
            'extension_version':version,'browser_control_protocol':control_protocol,
            'issues':issues,'coverage':'已配置账户及指定分组内的可见汇总；不认证全量覆盖'}
        self.store.patch_diagnostic('extension',state)
        return {'ok':True,'mode':'read_only'}

    def ai_analysis(self):
        if not self.ai_lock.acquire(blocking=False):
            raise ValueError("AI 分析正在进行")
        try:
            if time.monotonic() - self.last_ai_at < 900:
                raise ValueError("两次 AI 请求至少间隔 15 分钟")
            snapshots, evaluation = self.current()
            if not snapshots:
                raise ValueError("没有当前配置账户的数据；请先连接采集或导入报告")
            cfg = self.cfg()["ai"]
            if not cfg.get("enabled") or not os.environ.get("QIANCHUAN_AI_API_KEY"):
                raise ValueError("请先配置 AI 地址、模型并设置 API 密钥环境变量")
            package = make_package(snapshots, evaluation)
            self.last_ai_at = time.monotonic()
            self.store.audit("ai_requested", {"destination": urlsplit(cfg["endpoint"]).netloc,
                                               "metrics_count": len(package["metrics"]), "executed": False})
            result = request_analysis(cfg, package, evaluation)
            self.store.diagnostic("ai", {"last_result": result, "state": "completed"})
            self.store.audit("ai_validated", {"accepted": len(result["accepted_for_simulation"]), "rejected": len(result["rejected"]), "executed": False})
            return {"ok": True, **result}
        finally:
            self.ai_lock.release()

    def poll_once(self):
        cycle_id = self.store.begin_cycle(self.run_id, now_cn().isoformat())
        try:
            result = self.refresh_legacy()
            self.current()
        except Exception as exc:
            self.store.finish_cycle(cycle_id, now_cn().isoformat(), error_type=type(exc).__name__)
            LOG.error("collector failed: %s", type(exc).__name__)
            self.store.diagnostic("collector", {"state": "error", "error": type(exc).__name__})
            return {'state': 'error'}
        self.store.finish_cycle(cycle_id, now_cn().isoformat(), added=result.get('added', 0))
        self.store.diagnostic('collector', {'state': 'ok', 'added': result.get('added', 0), 'error': None})
        return {'state': 'ok'}

    def poll(self):
        while not self.stop_event.is_set():
            try:
                self.poll_once()
            except Exception as exc:
                LOG.error("collector failed: %s", type(exc).__name__)
                self.store.diagnostic("collector", {"state": "error", "error": type(exc).__name__})
            self.stop_event.wait(max(30, self.cfg().get("poll_seconds", 60)))

    def poll_official(self):
        while not self.stop_event.is_set():
            steps=(('chase_recovery',self.official_actions.hybrid.tick),('rule_recovery',self.official_actions.hybrid_rules.tick),
                ('schedules',self.official_actions.schedules.tick),('followups',self.official_actions.tick_followups),
                ('rule_auto',self.official_actions.rule_auto.tick),('budget_recovery',self.official_actions.budget_recovery.tick),
                ('chase_auto',self.official_actions.auto.tick))
            for name,step in steps:
                try:step()
                except Exception as exc:LOG.error('official action scheduler %s failed: %s',name,type(exc).__name__)
            try:self.official_api.poll_next(self.cfg()['accounts'])
            except Exception as exc:LOG.error('official API poll failed: %s',type(exc).__name__)
            self.stop_event.wait(5)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = "QianchuanReadOnly/1.0"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    @property
    def app(self):
        return self.server.app

    def log_message(self, fmt, *args):
        # Avoid logging query strings or headers containing pairing data.
        LOG.debug("HTTP %s %s", self.command, urlsplit(self.path).path)

    def valid_host(self):
        return self.headers.get("Host") in {f"127.0.0.1:{self.app.port}", f"localhost:{self.app.port}"}

    def extension_auth(self):
        supplied = self.headers.get("X-Monitor-Token", "")
        return bool(supplied) and hmac.compare_digest(supplied, self.app.token)

    def local_auth(self):
        origin = self.headers.get("Origin")
        expected = {f"http://127.0.0.1:{self.app.port}", f"http://localhost:{self.app.port}"}
        return (origin is None or origin in expected) and hmac.compare_digest(self.headers.get("X-Monitor-CSRF", ""), self.app.csrf)

    def send_data(self, data, status=200, content_type="application/json; charset=utf-8", extension_cors=False):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        origin = self.headers.get("Origin", "")
        if extension_cors and origin.startswith("chrome-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        try:
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
            pass

    def read_json(self):
        if self.headers.get_content_type() != "application/json":
            raise ValueError("请求类型必须为 application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("请求长度无效") from None
        if not 0 < length <= 2_000_000:
            raise ValueError("请求正文须为 1 字节到 2MB")
        return json.loads(self.rfile.read(length), object_pairs_hook=reject_duplicate_keys, parse_constant=lambda x: (_ for _ in ()).throw(ValueError("不允许 NaN/Infinity")))

    def do_OPTIONS(self):
        if not self.valid_host() or urlsplit(self.path).path not in CLOSE_WORKER_PATHS | HYBRID_WORKER_PATHS | {"/api/ingest", "/api/heartbeat", "/api/collector-config", "/api/browser-actions/worker-observe", "/api/browser-actions/worker-claim", "/api/browser-actions/worker-begin", "/api/browser-actions/worker-receipt", "/api/cruise/worker-jobs", "/api/cruise/worker-job-result", "/api/cruise/actions/worker-observe", "/api/cruise/actions/worker-claim", "/api/cruise/actions/worker-begin", "/api/cruise/actions/worker-receipt", "/api/cruise/batch/worker-observe", "/api/cruise/batch/worker-claim", "/api/cruise/batch/worker-begin", "/api/cruise/batch/worker-receipt", "/api/cruise/batch/worker-readback-jobs", "/api/cruise/batch/worker-readback-result"}:
            return self.send_data({"error": "forbidden"}, 403)
        origin = self.headers.get("Origin", "")
        if not origin.startswith("chrome-extension://"):
            return self.send_data({"error": "forbidden"}, 403)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Monitor-Token")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self.valid_host():
            return self.send_data({"error": "Host 不在本地白名单内"}, 403)
        if self.app.release:
            try:self.app.release.check()
            except ValueError as exc:return self.send_data({'error':str(exc)},403)
        if urlsplit(self.path).path == '/oauth/oceanengine/callback':
            # A top-level OAuth navigation is cross-site by design. Bind it to
            # the one-use state AND the HttpOnly cookie set by local UI POST.
            from http.cookies import SimpleCookie
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get('Cookie', ''))
                nonce = cookie['qc_api_oauth'].value if 'qc_api_oauth' in cookie else ''
                self.app.official_api.finish_authorization(parse_qs(urlsplit(self.path).query), nonce)
                self.app.official_api.record_authorization_result(True)
                outcome = 'connected'
            except Exception as error:
                self.app.official_api.record_authorization_result(False, error)
                outcome = 'failed'
            self.send_response(303)
            self.send_header('Location', '/api-setup?oauth=' + outcome)
            self.send_header('Set-Cookie', 'qc_api_oauth=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/oauth/oceanengine/callback')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if urlsplit(self.path).path in {'/api/collector-config', '/api/cruise/worker-jobs'}:
            origin=self.headers.get('Origin')
            if not self.extension_auth() or (origin is not None and not origin.startswith('chrome-extension://')):
                return self.send_data({'error':'采集器请求校验失败'},403,extension_cors=True)
            try:return self.send_data(self.app.cruise_worker_jobs() if urlsplit(self.path).path == '/api/cruise/worker-jobs' else collection_policy(self.app.cfg()),extension_cors=True)
            except ValueError:return self.send_data({'error':'本地采集配置无效'},400,extension_cors=True)
        # Block cross-origin browser reads, including credential/token bootstrap.
        origin = self.headers.get("Origin")
        if origin and origin not in {f"http://127.0.0.1:{self.app.port}", f"http://localhost:{self.app.port}"}:
            return self.send_data({"error": "拒绝跨站读取"}, 403)
        path = urlsplit(self.path).path
        if path == '/api/release/status' and self.app.release:
            return self.send_data(self.app.release.status())
        try:
            if path == "/api/status":
                return self.send_data(self.app.status())
            if path == '/api/official-api':
                return self.send_data({**self.app.official_api.status(self.app.port), 'csrf': self.app.csrf})
            if path == '/api/cruise':
                if parse_qs(urlsplit(self.path).query).get('view') == ['console']:
                    snapshot = self.app.console_status()
                    if snapshot is None:
                        return self.send_data({'error':'工作台状态仍在更新，稍后自动重试。'}, 503)
                    return self.send_data(snapshot)
                return self.send_data(self.app.cruise_status())
            if path == '/api/branding':
                return self.send_data({'ok':True,'branding':self.app.branding.get()})
            if path == '/api/official-api/actions/history':
                query=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                if any(len(values)!=1 for values in query.values()):raise ValueError('调控记录查询参数重复')
                return self.send_data(self.app.official_actions.activity_history({key:values[0] for key,values in query.items()}))
            if path == '/api/browser-actions':
                return self.send_data(self.app.browser_actions.status(collection_policy(self.app.cfg())))
            if path=='/api/accounts':
                return self.send_data(self.app.account_registry())
            if path=='/api/creative-workflows':
                return self.send_data(self.app.creative_workflows())
            if path == "/api/report":
                params = parse_qs(urlsplit(self.path).query)
                account_id = params.get('account_id',[None])[0]
                format_name = params.get('format',['json'])[0]
                if format_name not in {'json','markdown'}:
                    raise ValueError('报告格式应为json或markdown')
                report = self.app.report(account_id)
                if format_name == 'markdown':
                    return self.send_data(report_markdown(report).encode('utf-8'), content_type='text/markdown; charset=utf-8')
                return self.send_data(report)
            if path == "/api/pairing":
                return self.send_data({"token": self.app.token, "endpoint": f"http://127.0.0.1:{self.app.port}"})
            if path == "/api/history":
                params=parse_qs(urlsplit(self.path).query)
                account_id=params.get("account_id",[None])[0]
                if account_id is not None and account_id not in {a["id"] for a in self.app.cfg()["accounts"]}:
                    raise ValueError("账户不在观察范围内")
                allowed={a['id'] for a in self.app.cfg()['accounts']}
                rows=[r for r in self.app.store.evaluation_snapshots(account_id=account_id,series_key=params.get("series_key",[None])[0]) if r['account_id'] in allowed]
                return self.send_data({"snapshots":rows,"mode":"read_only","window_hours":28,"includes_stale_source_heads":True})
            if path == "/api/audit":
                return self.send_data({"rows": self.app.store.audit_rows()})
            if path == "/api/events":
                allowed={a['id'] for a in self.app.cfg()['accounts']}
                return self.send_data({"events":[r for r in self.app.store.events() if r['account_id'] in allowed],"executed":False,"window_hours":28,"includes_last_known_reports":True})
            if path == "/api/ai-preview":
                snapshots, evaluation = self.app.current()
                return self.send_data(make_package(snapshots, evaluation))
            files = {"/": ("cruise.html", "text/html; charset=utf-8"),
                     "/api-setup": ("api-setup.html", "text/html; charset=utf-8"),
                     "/api-setup.js": ("api-setup.js", "text/javascript; charset=utf-8"),
                     "/api-setup.css": ("api-setup.css", "text/css; charset=utf-8"),
                     "/cruise": ("cruise.html", "text/html; charset=utf-8"),
                     "/monitor": ("index.html", "text/html; charset=utf-8"),
                     "/cruise.js": ("cruise.js", "text/javascript; charset=utf-8"),
                     "/branding.js": ("branding.js", "text/javascript; charset=utf-8"),
                     "/official-workflow.js": ("official-workflow.js", "text/javascript; charset=utf-8"),
                     "/plan-metrics.js": ("plan-metrics.js", "text/javascript; charset=utf-8"),
                     "/activity-log.js": ("activity-log.js", "text/javascript; charset=utf-8"),
                     "/hybrid-chase.js": ("hybrid-chase.js", "text/javascript; charset=utf-8"),
                     "/activity-log.css": ("activity-log.css", "text/css; charset=utf-8"),
                     "/batch-chase.js": ("batch-chase.js", "text/javascript; charset=utf-8"),
                     "/live-close.js": ("live-close.js", "text/javascript; charset=utf-8"),
                     "/plan-metrics.css": ("plan-metrics.css", "text/css; charset=utf-8"),
                     "/cruise.css": ("cruise.css", "text/css; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/browser-control.js": ("browser-control.js", "text/javascript; charset=utf-8"),
                     "/styles.css": ("styles.css", "text/css; charset=utf-8"), "/rules.json": ("../docs/official-rules.json", "application/json; charset=utf-8")}
            if path in files:
                name, mime = files[path]
                asset = 'docs/official-rules.json' if path == '/rules.json' else 'static/' + name
                return self.send_data(read_asset(self.app.root, asset), content_type=mime)
            return self.send_data({"error": "路径不存在"}, 404)
        except ValueError as exc:
            return self.send_data({"error":str(exc)[:400]},400)
        except Exception as exc:
            LOG.error("read failed: %s", type(exc).__name__)
            return self.send_data({"error": "读取失败，请查看本地服务日志"}, 500)

    def do_POST(self):
        path = urlsplit(self.path).path
        if self.app.release:
            if not self.valid_host():return self.send_data({'error':'Host 不在本地白名单内'},403)
            try:self.app.release.check()
            except ValueError as exc:return self.send_data({'error':str(exc)},403)
            if path == '/api/release/ego-bootstrap':
                if self.headers.get('Origin') is not None:
                    return self.send_data({'error':'无效的执行器连接'},403)
                boot = self.app.release.take_ego_payload(self.headers.get('X-QCX-Bootstrap',''))
                return self.send_data(boot if boot else {'error':'执行器启动许可失效'},200 if boot else 403)
        if path.startswith('/api/ego-executor/worker/'):
            worker=self.app.ego_executor.credential(self.headers.get('X-Qianchuan-Ego'))
            if not self.valid_host() or self.headers.get('Origin') is not None or worker is None:
                return self.send_data({'error':'Ego 本机执行连接校验失败'},403)
            try:
                return self.send_data(self.app.ego_executor.dispatch(path.rsplit('/',1)[1],self.read_json(),worker=worker))
            except ValueError as exc:
                return self.send_data({'error':str(exc)[:400]},400)
            except Exception as exc:
                LOG.error('Ego dispatch failed: %s',type(exc).__name__)
                return self.send_data({'error':'Ego 阶段未完成，原单已保留'},500)
        hybrid_workers=HYBRID_WORKER_PATHS
        ext = path in CLOSE_WORKER_PATHS | hybrid_workers | {"/api/ingest", "/api/heartbeat", "/api/browser-actions/worker-observe", "/api/browser-actions/worker-claim", "/api/browser-actions/worker-begin", "/api/browser-actions/worker-receipt", "/api/cruise/worker-job-result", "/api/cruise/actions/worker-observe", "/api/cruise/actions/worker-claim", "/api/cruise/actions/worker-begin", "/api/cruise/actions/worker-receipt", "/api/cruise/batch/worker-observe", "/api/cruise/batch/worker-claim", "/api/cruise/batch/worker-begin", "/api/cruise/batch/worker-receipt", "/api/cruise/batch/worker-readback-jobs", "/api/cruise/batch/worker-readback-result"}
        worker_origin = self.headers.get('Origin')
        worker_path = path in CLOSE_WORKER_PATHS | hybrid_workers or path.startswith('/api/browser-actions/worker-') or path.startswith('/api/cruise/actions/worker-') or path.startswith('/api/cruise/batch/worker-') or path == '/api/cruise/worker-job-result'
        worker_origin_valid = not worker_path or worker_origin is None or worker_origin.startswith('chrome-extension://')
        if not self.valid_host() or not worker_origin_valid or not (self.extension_auth() if ext else self.local_auth()):
            # Drain a small rejected request so Windows does not reset the socket
            # before the client receives its 403 response. Never parse or act on it.
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if 0 < length <= 4096:
                    self.rfile.read(length)
            except (ValueError, OSError):
                pass
            return self.send_data({"error": "本地请求校验失败"}, 403, extension_cors=ext)
        try:
            payload = self.read_json()
            if path=='/api/branding':
                return self.send_data({'ok':True,'branding':self.app.branding.save(payload)})
            if path in hybrid_workers:
                if not self.app.ego_executor.legacy_allowed():
                    return self.send_data({'ok':True,'command':None,'allow':False,'message':'网页任务已迁移到 Ego Lite，原扩展不再领取或提交'},extension_cors=True)
                if path in RULE_WORKER_PATHS:
                    return self.send_data(getattr(self.app.official_actions.hybrid_rules,path.rsplit('worker-',1)[1])(payload),extension_cors=True)
                method={'worker-next':'next','worker-result':'result','worker-begin':'begin','worker-final':'final','worker-ping':'ping'}[path.rsplit('/',1)[-1]]
                return self.send_data(getattr(self.app.official_actions.hybrid,method)(payload),extension_cors=True)
            if path=='/api/ego-executor/configure':
                return self.send_data(self.app.ego_executor.configure(payload))
            if path=='/api/ego-executor/resume':
                return self.send_data(self.app.ego_executor.resume(payload))
            if path=='/api/ego-executor/pool':
                return self.send_data(self.app.ego_executor.pool.configure(payload))
            if path=='/api/ego-executor/account':
                return self.send_data(self.app.ego_executor.pool.control(payload))
            if path=='/api/official-api/hybrid/configure':
                return self.send_data(self.app.official_actions.hybrid.configure(payload))
            if path.startswith('/api/official-api/hybrid-rules/'):
                operation=path.rsplit('/',1)[1]
                if operation not in {'preview','retry','stop'}:raise ValueError('未知规则网页操作')
                return self.send_data(self.app.official_actions.hybrid_rules.control(payload,operation))
            if path=='/api/official-api/hybrid/stop':
                return self.send_data(self.app.official_actions.hybrid.stop(payload))
            if path=='/api/official-api/hybrid/retry':
                return self.send_data(self.app.official_actions.hybrid.retry(payload))
            if path=='/api/official-api/hybrid/recheck':
                return self.send_data(self.app.official_actions.hybrid.recheck(payload))
            if path=='/api/official-api/hybrid/start':
                return self.send_data(self.app.official_actions.hybrid.start(payload))
            if path=='/api/official-api/hybrid/start-test':
                return self.send_data(self.app.official_actions.hybrid.start_test(payload))
            if path=='/api/official-api/material-exclusions':
                return self.send_data(self.app.official_actions.save_material_exclusions(payload))
            if path=='/api/official-api/material-deferrals/clear':
                return self.send_data(self.app.official_actions.clear_deferrals(payload))
            if path=='/api/official-api/hybrid/preview':
                if not isinstance(payload,dict) or set(payload)!={'account_id'}:raise ValueError('请指定只读验证账户')
                if not self.app.official_actions.hybrid.request(payload['account_id'],reason='用户请求只读验证网页',preview=True):
                    raise ValueError('本户原调控网页阶段尚未结束，暂未安排新的只读预演；请等待原阶段完成后重试')
                return self.send_data({'ok':True,'ad_changes':0})
            if path=='/api/official-api/hybrid/probe':
                return self.send_data(self.app.official_actions.hybrid.request_probe(payload))
            if path == '/api/official-api/configure':
                result = self.app.official_api.configure(payload, self.app.port)
            elif path == '/api/official-api/authorize':
                if not isinstance(payload, dict) or set(payload) != {'label'}:
                    raise ValueError('需要本次授权名称')
                result = self.app.official_api.start_authorization(payload['label'])
                nonce = result.pop('browser_nonce')
                raw = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Set-Cookie', 'qc_api_oauth=' + nonce + '; Max-Age=600; HttpOnly; SameSite=Lax; Path=/oauth/oceanengine/callback')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            elif path == '/api/official-api/accounts':
                if not isinstance(payload, dict) or set(payload) != {'grant_id'}:
                    raise ValueError('需要明确授权连接')
                result = self.app.official_api.discover(payload['grant_id'])
            elif path == '/api/official-api/workflow':
                if not isinstance(payload,dict) or set(payload)!={'account_id','plan_id','materials'} or type(payload['materials']) is not bool:
                    raise ValueError('需要明确账户、计划和是否扫描素材')
                account=next((a for a in self.app.cruise_accounts() if a['id']==payload['account_id'] and a.get('observe',True)),None)
                if account is None:raise ValueError('账户未登记或监测已关闭')
                profile,_=self.app.batch_profile(account['id'])
                result={'ok':True,'result':self.app.official_api.scan_workflow(account,profile,payload['plan_id'],materials=payload['materials']),'ad_changes':0}
            elif path.startswith('/api/official-api/rule-auto/'):
                name=path.rsplit('/',1)[-1]
                if name not in {'configure','disable'}:raise ValueError('统一自动授权路径不存在')
                result=getattr(self.app.official_actions.rule_auto,name)(payload)
            elif path.startswith('/api/official-api/auto/'):
                name=path.rsplit('/',1)[-1]
                if name not in {'configure','disable'}:raise ValueError('自动执行配置路径不存在')
                result=getattr(self.app.official_actions.auto,name)(payload)
            elif path.startswith('/api/official-api/actions/'):
                name=path.rsplit('/',1)[-1]
                if name not in {'prepare','confirm','cancel','recheck','recheck_followup','hold_task','resume_task'}:raise ValueError('API执行路径不存在')
                if name in {'prepare','confirm'}:
                    with self.app.official_actions.local_claim_gate():
                        policy=collection_policy(self.app.cfg())
                        queues=(self.app.browser_actions.status(policy),self.app.cruise_actions.status(policy),self.app.batch_chase.status(policy),self.app.live_close.status(policy))
                        if any(r.get('state') in {'draft','approved','claimed','submitting','unknown','submitted_pending_verification'} for q in queues for r in q.get('actions',q.get('schedules',[]))):
                            raise ValueError('先处理原浏览器执行队列中的待确认或结果不明动作，避免两个执行器同时操作')
                result={'ok':True,'record':getattr(self.app.official_actions,name)(payload)}
            elif path in {'/api/official-api/read','/api/official-api/plans'}:
                if not isinstance(payload, dict) or set(payload) != {'account_id', 'grant_id'}:
                    raise ValueError('需要明确账户与授权连接')
                account = next((a for a in self.app.cfg()['accounts'] if a['id'] == payload['account_id']), None)
                if account is None:
                    raise ValueError('账户尚未加入本机监测范围')
                result = (self.app.official_api.read_account if path.endswith('/read') else self.app.official_api.read_plans)(account, payload['grant_id'])
            elif path.startswith('/api/browser-actions/'):
                result = self.app.browser_action_call(path.rsplit('/', 1)[-1], payload)
            elif path.startswith('/api/cruise/'):
                result = self.app.cruise_call(path, payload)
            elif path == "/api/ingest":
                channel = "codex_plugin" if isinstance(payload, dict) and payload.get("collector") == "codex_plugin" else "extension"
                result = self.app.ingest(payload, channel)
            elif path == "/api/heartbeat":
                result = self.app.heartbeat(payload)
            elif path=='/api/accounts':
                result=self.app.save_account_registry(payload)
            elif path == "/api/import":
                result = self.app.ingest(payload, "import")
            elif path=='/api/official-report-preview':
                result=self.app.official_report(payload)
            elif path=='/api/custom-report-review':
                result={'ok':True,**review_custom_reports(payload,self.app.cfg())}
            elif path=='/api/material-readback-review':
                result={'ok':True,**review_supplied_materials(payload,self.app.cfg())}
            elif path=='/api/control-creation-review':
                result={'ok':True,**review_supplied_creation(payload,self.app.cfg())}
            elif path=='/api/chase-creation-preview':
                cfg=self.app.cfg();at=now_cn()
                snapshots,evaluation=self.app.current(cfg=cfg,at=at)
                result={'ok':True,**preview_material_chase(payload,snapshots,cfg,evaluation,at)}
            elif path=='/api/creative-rotation-preview':
                cfg=self.app.cfg();at=now_cn()
                snapshots,evaluation=self.app.current(cfg=cfg,at=at)
                result={'ok':True,**preview_creative_rotations(payload,snapshots,cfg,evaluation,at)}
            elif path=='/api/creative-workflow-create':
                result=self.app.create_creative_workflow(payload)
            elif path=='/api/creative-workflow-cancel':
                result=self.app.cancel_creative_workflow(payload)
            elif path=='/api/official-report-import':
                result=self.app.official_report(payload,persist=True)
            elif path == "/api/event-import":
                result = self.app.import_events(payload)
            elif path == "/api/migration-plan":
                result = self.app.migration_plan(payload)
            elif path == "/api/migration-cancel":
                if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
                    raise ValueError("缺少本地交接计划ID")
                self.app.store.cancel_migration(payload["id"])
                self.app.store.audit("budget_migration_cancelled", {"id": payload["id"], "executed": False})
                result = {"ok": True, "executed": False, "note": "仅取消本地交接计划，不操作账户"}
            elif path == "/api/settings":
                result = self.app.save_settings(payload)
            elif path == "/api/refresh":
                result = {"ok": True, **self.app.refresh_legacy()}
            elif path == "/api/simulate":
                name = payload.get("scenario", "low_roi")
                if name not in {"low_roi", "stable_growth","bid_drop_recovery","budget_migration","emergency_stop","traffic_quality","decline_maintenance","roi_tuning","cold_start","roi_recovery","live_exclusion","roi_recovery_cross_day","product_carrying","creative_idle","creative_rotation","creative_workflow","control_creation","creation_readback","control_deadline","material_chase_creation","smart_control"}:
                    raise ValueError("未知演示场景")
                migrations=[]
                if name=="budget_migration":
                    rows,cfg,when,events,migrations=migration_scenario(self.app.cfg())
                elif name=="bid_drop_recovery":
                    rows,cfg,when,events=outcome_scenario(self.app.cfg())
                elif name=='emergency_stop':
                    rows,cfg,when=emergency_scenario(self.app.cfg());events=[]
                elif name=='traffic_quality':
                    rows,cfg,when=quality_scenario(self.app.cfg());events=[]
                elif name=='decline_maintenance':
                    rows,cfg,when,events=maintenance_scenario(self.app.cfg())
                elif name=='roi_tuning':
                    rows,cfg,when=roi_tuning_scenario(self.app.cfg());events=[]
                elif name=='cold_start':
                    rows,cfg,when=cold_start_scenario(self.app.cfg());events=[]
                elif name=='roi_recovery':
                    rows,cfg,when,events=roi_recovery_scenario(self.app.cfg())
                elif name=='roi_recovery_cross_day':
                    rows,cfg,when,events=roi_recovery_cross_day_scenario(self.app.cfg())
                elif name=='product_carrying':
                    rows,cfg,when=product_carrying_scenario(self.app.cfg());events=[]
                elif name=='live_exclusion':
                    rows,cfg,when=live_exclusion_scenario(self.app.cfg());events=[]
                elif name=='creative_idle':
                    rows,cfg,when=creative_idle_scenario(self.app.cfg());events=[]
                elif name in {'creative_rotation','creative_workflow'}:
                    rows,cfg,when,registry=creative_rotation_scenario(self.app.cfg());events=[]
                elif name in {'control_creation','creation_readback','control_deadline'}:
                    rows,cfg,when=control_creation_scenario(self.app.cfg());events=[]
                elif name=='material_chase_creation':
                    rows,cfg,when,chase_payload=chase_creation_scenario(self.app.cfg());events=[]
                elif name=='smart_control':
                    rows,cfg,when=smart_control_scenario(self.app.cfg());events=[]
                else:
                    rows,cfg,when=scenario(self.app.cfg(),name);events=[]
                result = {"ok": True, "synthetic": True, "simulation_at": when.isoformat(),
                          "evaluation": evaluate_all(rows, cfg, when,events=events,migrations=migrations), "snapshots": rows, "events":events,"persisted": False, "executed": False}
                if name=='creative_rotation':
                    result['creative_rotation']=preview_creative_rotations(registry,rows,cfg,result['evaluation'],when)
                if name=='creative_workflow':
                    result['creative_workflow']=creative_workflow_replay(self.app.cfg())
                if name=='control_creation':
                    from .control_creation import prepare_boost_body,CREATE_PATH,CREATE_SOURCE
                    p=next((p for p in result['evaluation']['recommendations'] if p['action']=='one_click_boost' and p['eligible']),None)
                    if p is None:raise ValueError('合成起量场景没有当前合格建议')
                    parent=next(r for r in rows if r['id']=='demo-create-24')
                    body=prepare_boost_body(p,parent,cfg,when,rows,result['evaluation'],synthetic=True)
                    result['control_creation']={'synthetic':True,'account_id':p['account_id'],'parent_plan_id':p['entity_id'],
                        'request':{'method':'POST','url':None,'documented_endpoint':CREATE_PATH,'body_json':body,'official_source':CREATE_SOURCE},
                        'executed':False,'persisted':False,'native_client_enabled':False,
                        'chase_blocker':'直播控成本追投默认长期有效，当前合同不支持2小时到期；可靠关停尚未接通。'}
                if name=='creation_readback':
                    result['creation_readback']=creation_readback_example(self.app.cfg())
                if name=='control_deadline':
                    result['control_deadline']=deadline_example()
                if name=='material_chase_creation':
                    result['material_chase_creation']=preview_material_chase(chase_payload,rows,cfg,result['evaluation'],when,synthetic=True)
                if name=='smart_control':
                    result['smart_control']=smart_control_preview(rows,cfg,when,result['evaluation'])
                self.app.store.audit("offline_simulation", {"scenario": name, "executed": False, "production_snapshots_changed": False})
            elif path == "/api/ai-analyze":
                result = self.app.ai_analysis()
            elif path == "/api/action-preview":
                if not isinstance(payload,dict) or not isinstance(payload.get("recommendation_id"),str):
                    raise ValueError("缺少本地建议ID")
                cfg=self.app.cfg();at=now_cn()
                snapshots,evaluation=self.app.current(cfg=cfg,at=at)
                proposal=next((r for r in evaluation["recommendations"] if r["id"]==payload["recommendation_id"]),None)
                if proposal is None:
                    raise ValueError("当前本地规则中没有这条建议")
                entities={(e["account_id"],e["entity_id"],e['scope'],e["source_key"]):e for e in evaluation["entities"]}
                entity=entities.get((proposal["account_id"],proposal["entity_id"],proposal['scope'],proposal.get("source_key")))
                snapshot=next((s for s in snapshots if entity and s["id"]==entity["latest_snapshot_id"]),None)
                if snapshot is None:
                    raise ValueError("建议来源快照已缺失，请重新观察")
                result=prepare_action(proposal,snapshot,cfg,at,snapshots=snapshots,evaluation=evaluation)
                self.app.store.audit("action_request_preview",{"proposal_id":proposal["id"],"supported":result["supported_for_preview"],"executed":False})
            else:
                return self.send_data({"error": "此程序没有账户执行接口"}, 404, extension_cors=ext)
            return self.send_data(result, extension_cors=ext)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            return self.send_data({"error": str(exc)[:400]}, 400, extension_cors=ext)
        except Exception as exc:
            LOG.error("request failed: %s", type(exc).__name__)
            return self.send_data({"error": "处理失败，请查看本地服务日志"}, 500, extension_cors=ext)


def main(argv=None, *, root=ROOT, release=None):
    parser = argparse.ArgumentParser(description="千川多账户巡航与逐条确认调控")
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument("--open", action="store_true", help="在默认浏览器打开本地控制台")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("端口范围必须为1024-65535")
    root = Path(root)
    (root/"data").mkdir(exist_ok=True)
    logging.basicConfig(filename=root/"data"/"service.log", level=logging.INFO, encoding="utf-8", format="%(asctime)s %(levelname)s %(message)s")
    if release:
        release.check()
    app = App(root, port=args.port, release=release)
    try:
        server = Server(("127.0.0.1", args.port), Handler)
    except OSError:
        print(f"端口 {args.port} 已被占用；请检查是否已启动，或使用 --port 指定其他端口。")
        return
    server.app = app
    thread = threading.Thread(target=app.poll, daemon=True, name="read-only-collector")
    thread.start()
    cruise_thread = threading.Thread(target=app.cruise_loop, daemon=True, name="multi-account-cruise")
    cruise_thread.start()
    api_thread=threading.Thread(target=app.poll_official,daemon=True,name='official-api-reader')
    api_thread.start()
    app.ego_executor.start(app.stop_event)
    license_expired = threading.Event()

    def watch_license():
        while not app.stop_event.wait(1):
            try:release.check()
            except ValueError:
                license_expired.set()
                app.stop_event.set()
                server.shutdown()
                return

    if release:
        threading.Thread(target=watch_license, daemon=True, name='release-license').start()
    print(f"多账户巡航：http://127.0.0.1:{args.port}/\n未开启全自动时逐单确认；全自动追投须先填写本户额度并明确启用。按 Ctrl+C 停止。")
    if args.open:
        webbrowser.open(f"http://127.0.0.1:{args.port}/")
    try:
        server.serve_forever(poll_interval=.5)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop_event.set()
        server.server_close()
        thread.join(timeout=5)
        cruise_thread.join(timeout=5)
        api_thread.join(timeout=5)
        if app.ego_executor.thread:app.ego_executor.thread.join(timeout=5)
        app.store.audit("monitor_stopped", {"executed": False})
    return 78 if license_expired.is_set() else 0


if __name__ == "__main__":
    main()
