"""Default-off, disconnected SMART_BOOST producer for a future authorized use.

Serving never imports this module. A returned new task ID acknowledges creation,
not task usability; accepted/uncertain requests retain the parent attempt hold.
"""
import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime,timedelta

from .actuation import prepare_action
from .control_creation import interpret_creation_response,CONTRACT_VERSION,PURCHASE
from .evaluation import evaluate_all
from .plan_readback import _clock
from .schema import now_cn
from .native_control_io import create_task,qualified,_NativeReply,CREATE_PATH


@dataclass(frozen=True)
class ControlCreationPermit:
    account_id:str
    request_digest:str
    issued_at:datetime
    expires_at:datetime
    authorization_reference:str


def _basis(proposal_id,rows,cfg,at,events,migrations,recovery_rows,recovery_events):
    evaluation=evaluate_all(rows,cfg,at,events=events,migrations=migrations,
        recovery_snapshots=recovery_rows,recovery_events=recovery_events)
    proposal=next((p for p in evaluation['recommendations'] if p['id']==proposal_id and p['action']=='one_click_boost' and p['eligible'] is True),None)
    if proposal is None:raise ValueError('当前完整评估没有合格一键起量建议')
    entity=next((e for e in evaluation['entities'] if e['account_id']==proposal['account_id'] and e['entity_id']==proposal['entity_id']
        and e['scope']==proposal['scope'] and e['source_key']==proposal['source_key']),None)
    snapshot=next((r for r in rows if entity and r.get('id')==entity['latest_snapshot_id']),None)
    if snapshot is None:raise ValueError('缺少起量母计划当前来源')
    preview=prepare_action(proposal,snapshot,cfg,at,snapshots=rows,evaluation=evaluation)
    if not preview['supported_for_preview']:raise ValueError('母计划设置或创建合同尚未满足')
    return proposal,snapshot,preview


class ControlCreationClient:
    def __init__(self,ledger,*,enable_network=False,transport=None,clock=None):
        if transport is not None and not callable(transport):raise ValueError('创建传输器须为显式可调用对象')
        if clock is not None and not callable(clock):raise ValueError('创建时钟须为可调用对象')
        self.ledger=ledger;self.enabled=enable_network is True
        self.transport=transport;self.clock=now_cn if clock is None else clock

    def dispatch(self,attempt_id,proposal_id,snapshots,settings,permit,token_provider,*,events=(),migrations=(),recovery_snapshots=None,recovery_events=None,deadline_id=None):
        if not self.enabled:raise PermissionError('任务创建适配器默认关闭，当前服务没有创建入口')
        def materialize(value):return value if isinstance(value,list) else list(value)
        snapshots,events,migrations=map(materialize,(snapshots,events,migrations))
        recovery_snapshots=None if recovery_snapshots is None else materialize(recovery_snapshots)
        recovery_events=None if recovery_events is None else materialize(recovery_events)
        if not isinstance(attempt_id,str) or not 1<=len(attempt_id)<=150 or not attempt_id.isascii() or not all(c.isalnum() or c in '_-' for c in attempt_id):
            raise ValueError('创建尝试ID无效')
        began=_clock(self.clock())
        if (not isinstance(permit,ControlCreationPermit) or not isinstance(permit.issued_at,datetime)
            or not isinstance(permit.expires_at,datetime) or permit.issued_at.tzinfo is None or permit.expires_at.tzinfo is None
            or not permit.issued_at<=began<permit.expires_at or not timedelta(0)<permit.expires_at-permit.issued_at<=timedelta(minutes=5)
            or not isinstance(permit.authorization_reference,str) or not permit.authorization_reference.strip()):
            raise PermissionError('需要本次具体起量请求的有效授权记录')
        def frame():
            return copy.deepcopy((list(snapshots),settings,list(events),list(migrations),recovery_snapshots,recovery_events))
        rows,cfg,ev,mi,rr,re=frame();proposal,snapshot,preview=_basis(proposal_id,rows,cfg,began,ev,mi,rr,re)
        if permit.account_id!=proposal['account_id'] or permit.request_digest!=preview['digest']:
            raise PermissionError('起量账户或请求摘要与授权不符')
        try:token=token_provider()
        except Exception:raise ValueError('显式凭据提供器未返回可用凭据') from None
        if not isinstance(token,str) or not 1<=len(token)<=4096 or any(ord(c)<33 or ord(c)>126 for c in token):
            raise ValueError('开放平台凭据格式无效')
        at=_clock(self.clock())
        if not began<=at<permit.expires_at:raise PermissionError('创建授权已过期或时钟倒退')
        rows,cfg,ev,mi,rr,re=frame();proposal,snapshot,preview=_basis(proposal_id,rows,cfg,at,ev,mi,rr,re)
        if permit.account_id!=proposal['account_id'] or permit.request_digest!=preview['digest']:
            raise PermissionError('创建请求在提交前发生变化')
        operation=hashlib.sha256(json.dumps([preview['digest'],snapshot['id'],snapshot['source']['data_at']],ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
        context={'account_id':proposal['account_id'],'scope':'control_create','entity_id':None,
            'parent_plan_id':snapshot['entity_id'],'control_type':'one_click_boost','action':'one_click_boost',
            'proposal_id':proposal_id,'snapshot_id':snapshot['id'],'data_at':snapshot['source']['data_at'],
            'request_digest':preview['digest'],'contract':'control_create','cooldown_seconds':max(3600,cfg.get('rules',{}).get('cooldown_minutes',60)*60),
            'contract_version':CONTRACT_VERSION,'request_body_json':preview['request']['body_json'],
            'marketing_goal':'LIVE_PROM_GOODS','requested_external_action':PURCHASE,
            'requested_budget':proposal['parameters']['budget'],'requested_duration_hours':proposal['parameters']['duration_hours'],
            'authorization_reference_digest':hashlib.sha256(permit.authorization_reference.encode()).hexdigest()}
        if deadline_id is not None:context['deadline_id']=deadline_id
        self.ledger.claim(attempt_id,operation,at,context)
        # Recheck time and all rules after waiting for SQLite, before any IO.
        try:
            io_at=_clock(self.clock())
            if not at<=io_at<permit.expires_at:raise ValueError('创建授权已过期或时钟倒退')
            rows,cfg,ev,mi,rr,re=frame()
            _,_,last=_basis(proposal_id,rows,cfg,io_at,ev,mi,rr,re)
            if last['digest']!=preview['digest']:raise ValueError('创建意图变化')
            if deadline_id is not None:
                from .control_deadline import DeadlineLedger
                DeadlineLedger(self.ledger.path).ready_for_creation(deadline_id,preview['request']['body_json'],io_at)
        except Exception:
            result={'attempt_id':attempt_id,'submitted':False,'state':'failed_before_io','parent_hold_active':False,
                'automatic_retry':False,'platform_state_observed':False,'note':'动作前复核未完成，没有发送请求'}
            self.ledger.finish(attempt_id,'failed',result);return result
        try:
            reply=create_task(preview['request']['body_json'],token,10,self.clock) if self.transport is None else self.transport(preview['request']['body_json'],token,10)
            decoded=interpret_creation_response(reply.payload() if isinstance(reply,_NativeReply) else reply)
            trusted=qualified(reply) and reply.method=='POST' and reply.path==CREATE_PATH and reply.request_digest==preview['digest'] and reply.sent_at>=io_at
            receipt={'origin_verified':trusted,'request_digest':preview['digest'],
                'request_id_digest':reply.request_id_digest if trusted else None,
                'response_digest':hashlib.sha256(reply.raw.encode()).hexdigest() if trusted else None,
                'sent_at':reply.sent_at.isoformat() if trusted else None,
                'received_at':reply.received_at.isoformat() if trusted else None,
                'server_at':reply.server_at.isoformat() if trusted else None}
            deadline_bound=None
            if deadline_id is not None:
                from .control_deadline import DeadlineLedger
                try:
                    job=DeadlineLedger(self.ledger.path).bind_creation(deadline_id,preview['request']['body_json'],reply,self.clock(),owner_attempt_id=attempt_id)
                    deadline_bound=job['state'] in {'watching','cancelled_creation_rejected'}
                except Exception:deadline_bound=False
            state='failed' if decoded['known_rejected'] else 'unknown'
            result={'attempt_id':attempt_id,'submitted':True,'state':'accepted_pending_readback' if decoded['business_accepted'] else state,
                'ledger_state':state,'business_accepted':decoded['business_accepted'],'created_task_id':decoded['created_task_id'],
                'creation_receipt':receipt,
                'parent_plan_id':snapshot['entity_id'],'parent_hold_active':state=='unknown',
                'platform_state_observed':False,'automatic_retry':False,
                'note':'创建回执不证明任务可投；接受或未知结果需独立读回，不自动重发或解除占用'}
            if deadline_id is not None:result['deadline_binding_completed']=deadline_bound
        except Exception:
            state='unknown';result={'attempt_id':attempt_id,'submitted':True,'state':'unknown','ledger_state':'unknown',
                'business_accepted':False,'created_task_id':None,'parent_plan_id':snapshot['entity_id'],'parent_hold_active':True,
                'platform_state_observed':False,'automatic_retry':False,'note':'创建结果未知，保持母计划占用；原始异常不落库'}
        self.ledger.finish(attempt_id,state,result);return result
