"""Disconnected default-off producer requiring a current private deadline lease."""
import copy
from dataclasses import dataclass
from datetime import datetime,timedelta
import json

from .control_creation import PURCHASE,interpret_creation_response
from .control_deadline import DeadlineLedger
from .deadline_service import require_live_lease
from .evaluation import evaluate_all
from .material_chase_creation import preview_material_chase,VERSION
from .native_control_io import create_task,qualified,CREATE_PATH,digest
from .official_reports import _document,_guard
from .plan_readback import _clock
from .schema import now_cn


@dataclass(frozen=True)
class MaterialChasePermit:
    account_id:str
    request_digest:str
    qualification_digest:str
    deadline_id:str
    issued_at:datetime
    expires_at:datetime
    authorization_reference:str


def _basis(payload,rows,cfg,at,events,migrations,recovery_rows,recovery_events):
    evaluation=evaluate_all(rows,cfg,at,events=events,migrations=migrations,
        recovery_snapshots=recovery_rows,recovery_events=recovery_events)
    preview=preview_material_chase(payload,rows,cfg,evaluation,at)
    if preview['prepared'] is not True:raise ValueError('当前完整评估及素材追投合同没有同一合格创建意图')
    return preview['draft']


class MaterialChaseClient:
    def __init__(self,ledger,*,enable_network=False,clock=None):
        if clock is not None and not callable(clock):raise ValueError('追投时钟须为明确可调用对象')
        self.ledger=ledger;self.enabled=enable_network is True;self.clock=now_cn if clock is None else clock

    def dispatch(self,attempt_id,payload,snapshots,settings,permit,deadline_lease,token_provider,*,events=(),migrations=(),recovery_snapshots=None,recovery_events=None):
        if not self.enabled:raise PermissionError('素材追投创建默认关闭，当前观察服务不导入或启动该客户端')
        if not isinstance(attempt_id,str) or not 1<=len(attempt_id)<=100 or not attempt_id.isascii() or not all(c.isalnum() or c in '_-' for c in attempt_id):
            raise ValueError('追投创建尝试编号无效')
        def materialize(value):return value if isinstance(value,list) else list(value)
        snapshots,events,migrations=map(materialize,(snapshots,events,migrations))
        recovery_snapshots=None if recovery_snapshots is None else materialize(recovery_snapshots)
        recovery_events=None if recovery_events is None else materialize(recovery_events)
        began=_clock(self.clock())
        if (not isinstance(permit,MaterialChasePermit) or not isinstance(permit.issued_at,datetime)
            or not isinstance(permit.expires_at,datetime) or permit.issued_at.tzinfo is None or permit.expires_at.tzinfo is None
            or not permit.issued_at<=began<permit.expires_at or not timedelta(0)<permit.expires_at-permit.issued_at<=timedelta(minutes=5)
            or not isinstance(permit.authorization_reference,str) or not permit.authorization_reference.strip()):
            raise PermissionError('需要当前具体追投意图、截止和资格摘要的有效授权')
        def frame():
            document=_document(copy.deepcopy(payload));_guard(document)
            return copy.deepcopy((document,snapshots,settings,events,migrations,recovery_snapshots,recovery_events))
        def reviewed(at):
            doc,rows,cfg,ev,mi,rr,re=frame();draft=_basis(doc,rows,cfg,at,ev,mi,rr,re)
            if (permit.account_id!=draft['account_id'] or permit.request_digest!=draft['body_digest']
                or permit.qualification_digest!=draft['qualification_digest']):
                raise PermissionError('追投请求或当前资格与已审阅授权不一致')
            job=require_live_lease(deadline_lease,self.ledger.path,permit.deadline_id,draft['body_digest'],at)
            if (job['account_id']!=draft['account_id'] or job['parent_plan_id']!=draft['parent_plan_id']
                or job['material_id']!=draft['material_id'] or job['duration_hours']!=draft['requested_local_duration_hours']):
                raise PermissionError('当前活跃截止预留与追投身份或时长不一致')
            return draft,rows,cfg
        draft,rows,cfg=reviewed(began)
        try:token=token_provider()
        except Exception:raise ValueError('显式凭据提供器未返回可用凭据') from None
        if not isinstance(token,str) or not 1<=len(token)<=4096 or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError('开放平台凭据格式无效')
        at=_clock(self.clock())
        if not began<=at<permit.expires_at:raise PermissionError('追投创建授权过期或时钟倒退')
        draft,rows,cfg=reviewed(at)
        snapshot=next(r for r in rows if r['id']==draft['creative_snapshot_id'])
        context={'scope':'control_create','entity_id':None,'account_id':draft['account_id'],'parent_plan_id':draft['parent_plan_id'],
            'control_type':'material_chase','action':'create_chase','material_id':draft['material_id'],
            'proposal_id':draft['proposal_id'],'snapshot_id':draft['creative_snapshot_id'],'parent_snapshot_id':draft['parent_snapshot_id'],
            'data_at':snapshot['source']['data_at'],'request_digest':draft['body_digest'],'qualification_digest':draft['qualification_digest'],
            'request_body_json':draft['body_json'],'contract':'control_create','contract_version':VERSION,
            'marketing_goal':'LIVE_PROM_GOODS','requested_external_action':PURCHASE,
            'requested_budget':float(draft['budget']),'requested_duration_hours':draft['requested_local_duration_hours'],
            'deadline_id':permit.deadline_id,'cooldown_seconds':max(3600,cfg.get('rules',{}).get('cooldown_minutes',60)*60),
            'authorization_reference_digest':digest(permit.authorization_reference)}
        operation=digest(json.dumps([draft['body_digest'],draft['qualification_digest']],separators=(',',':')))
        self.ledger.claim(attempt_id,operation,at,context)
        deadlines=DeadlineLedger(self.ledger.path)
        try:
            io=_clock(self.clock())
            if not at<=io<permit.expires_at:raise ValueError('追投创建授权过期或时钟倒退')
            final,_,_=reviewed(io)
            if final['qualification_digest']!=draft['qualification_digest']:raise ValueError('SQLite等待后追投资格已改变')
            deadlines.ready_for_creation(permit.deadline_id,draft['body_json'],io)
        except Exception:
            result={'attempt_id':attempt_id,'submitted':False,'state':'failed_before_io','parent_hold_active':True,
                'deadline_hold_active':True,'automatic_retry':False,'platform_state_observed':False}
            self.ledger.finish(attempt_id,'failed',result);return result
        try:
            reply=create_task(draft['body_json'],token,10,self.clock);decoded=interpret_creation_response(reply.payload())
            native=(qualified(reply) and reply.method=='POST' and reply.path==CREATE_PATH and reply.request_digest==draft['body_digest'] and reply.sent_at>=io)
            receipt={'origin_verified':native,'request_digest':draft['body_digest'],'response_digest':digest(reply.raw) if native else None,
                'request_id_digest':reply.request_id_digest if native else None,'sent_at':reply.sent_at.isoformat() if native else None,
                'received_at':reply.received_at.isoformat() if native else None,'server_at':reply.server_at.isoformat() if native else None}
            bound=False
            try:
                job=deadlines.bind_creation(permit.deadline_id,draft['body_json'],reply,self.clock(),owner_attempt_id=attempt_id)
                bound=job['state'] in {'watching','cancelled_creation_rejected'}
            except Exception:pass
            state='failed' if decoded['known_rejected'] else 'unknown'
            result={'attempt_id':attempt_id,'submitted':True,'state':'accepted_pending_readback' if decoded['business_accepted'] else state,
                'ledger_state':state,'business_accepted':decoded['business_accepted'],'created_task_id':decoded['created_task_id'],
                'parent_plan_id':draft['parent_plan_id'],'material_id':draft['material_id'],'creation_receipt':receipt,
                'deadline_binding_completed':bound,'deadline_id':permit.deadline_id,
                'parent_hold_active':not (state=='failed' and bound),'platform_state_observed':False,'automatic_retry':False}
        except Exception:
            state='unknown';result={'attempt_id':attempt_id,'submitted':True,'state':'unknown','ledger_state':'unknown',
                'business_accepted':False,'created_task_id':None,'deadline_id':permit.deadline_id,'parent_hold_active':True,
                'platform_state_observed':False,'automatic_retry':False,'note':'追投创建结果未知，不按名称猜测或重发；保持母计划和截止占用'}
        self.ledger.finish(attempt_id,state,result);return result
