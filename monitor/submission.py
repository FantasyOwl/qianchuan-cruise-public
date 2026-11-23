"""Disconnected plan API adapters for a future explicitly authorized deployment.

The running observation server never imports or instantiates this module. No
credentials are loaded on import. Network is disabled by default, and a short
lived, request-specific permit and a fresh local evaluation are required.
"""
from __future__ import annotations

import json
import hashlib
import copy
import sqlite3
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

from .actuation import BASE_URL, CONTRACTS, prepare_action, interpret_budget_response, interpret_status_response, interpret_roi_response
from .evaluation import evaluate_all
from .engine import _settings
from .schema import now_cn
from .plan_readback import _clock
from .control_contracts import interpret_control_budget_response, interpret_control_status_response
from .plan_locks import lock_identity as _lock_identity, check_reservations


@dataclass(frozen=True)
class _RequestPermit:
    """An integration boundary, not evidence that the current user authorized IO.

    A future operator must obtain the user's explicit authorization before
    constructing one. Neither configuration, AI output nor an HTTP payload in
    the observation server can construct or grant this permit.
    """
    account_id: str
    request_digest: str
    issued_at: datetime
    expires_at: datetime
    authorization_reference: str


@dataclass(frozen=True)
class BudgetPermit(_RequestPermit):
    pass


@dataclass(frozen=True)
class PlanStatusPermit(_RequestPermit):
    pass


@dataclass(frozen=True)
class RoiTargetPermit(_RequestPermit):
    pass


@dataclass(frozen=True)
class ControlBudgetPermit(_RequestPermit):
    pass


@dataclass(frozen=True)
class ControlStatusPermit(_RequestPermit):
    pass


class AttemptLedger:
    """Claim before IO. An uncertain attempt is never automatically retried."""
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS submission_attempts (
                          id TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
                          created_at TEXT NOT NULL, state TEXT NOT NULL, result_json TEXT, context_json TEXT)""")
            if 'context_json' not in {r[1] for r in db.execute('PRAGMA table_info(submission_attempts)')}:
                db.execute('ALTER TABLE submission_attempts ADD COLUMN context_json TEXT')
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS submitted_digest ON submission_attempts(request_digest)")
            db.execute("""CREATE TABLE IF NOT EXISTS supplied_readback_reviews (
                          attempt_id TEXT NOT NULL, evidence_digest TEXT NOT NULL,
                          checked_at TEXT NOT NULL, review_json TEXT NOT NULL,
                          PRIMARY KEY(attempt_id,evidence_digest))""")
            db.execute("""CREATE TABLE IF NOT EXISTS control_creation_observations (
                          attempt_id TEXT NOT NULL, observation_id TEXT NOT NULL,
                          received_at TEXT NOT NULL, trusted_origin INTEGER NOT NULL,
                          observation_json TEXT NOT NULL, PRIMARY KEY(attempt_id,observation_id))""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def claim(self, attempt_id, digest, now, context=None):
        try:
            with self.connect() as db:
                # Serialize checks and insertion across clients/processes.
                db.execute('BEGIN IMMEDIATE')
                if context:
                    current_identity = _lock_identity(context)
                    check_reservations(db,current_identity,context,now)
                    for created_at, state, raw_context in db.execute(
                            "SELECT created_at,state,context_json FROM submission_attempts WHERE state IN ('claimed','unknown','confirmed')"):
                        try:
                            prior = json.loads(raw_context or '{}')
                        except (TypeError, ValueError):
                            prior = {}
                        try:prior_identity = _lock_identity(prior)
                        except ValueError:
                            raise ValueError('历史提交缺少对象证据，需先独立对账，禁止继续提交')
                        if prior_identity != current_identity:
                            continue
                        if state in {'claimed', 'unknown'}:
                            raise ValueError('同账户同计划尚有未明确的提交，需先独立对账，禁止叠加操作')
                        try:
                            prior_at = datetime.fromisoformat(created_at)
                            cooldown = max(3600, context.get('cooldown_seconds', 3600), prior.get('cooldown_seconds', 3600))
                            in_cooldown = (now-prior_at).total_seconds() < cooldown
                        except (TypeError, ValueError):
                            raise ValueError('历史提交时间证据无效，需先核对，禁止继续提交') from None
                        if in_cooldown:
                            raise ValueError('同账户同计划尚未满足至少一小时的调控间隔')
                db.execute("INSERT INTO submission_attempts(id,request_digest,created_at,state,context_json) VALUES (?,?,?,'claimed',?)",
                           (attempt_id, digest, now.isoformat(), json.dumps(context or {}, ensure_ascii=False, allow_nan=False)))
        except sqlite3.IntegrityError:
            raise ValueError("该提交编号已使用；结果不明确时必须先对账，禁止重试") from None

    def finish(self, attempt_id, state, result):
        with self.connect() as db:
            db.execute("UPDATE submission_attempts SET state=?,result_json=? WHERE id=?",
                       (state, json.dumps(result, ensure_ascii=False), attempt_id))

    def review_supplied_readback(self, attempt_id, raw_response, query_context, now, *, response_kind='list'):
        """Append an offline comparison without changing the submission state.

        A supplied JSON file has no independently authenticated provenance.
        Even a matching value cannot resolve an uncertain network attempt.
        """
        from .plan_readback import compare_supplied_readback
        if response_kind == 'detail':
            from .plan_detail import compare_supplied_detail
            compare = compare_supplied_detail
        elif response_kind == 'control_list':
            from .control_readback import compare_supplied_task_pages
            compare = compare_supplied_task_pages
        elif response_kind == 'list':
            compare = compare_supplied_readback
        else:
            raise ValueError('只支持已核验的计划或任务读取响应')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT created_at,context_json FROM submission_attempts WHERE id=?',
                             (attempt_id,)).fetchone()
            if row is None:
                raise ValueError('找不到待核对的提交记录')
            try:
                context = json.loads(row[1] or '{}')
            except (TypeError, ValueError):
                raise ValueError('提交记录对象证据损坏') from None
            review = compare(raw_response, query_context, {'created_at': row[0], 'context': context}, now)
            identity_fields = {'response': review['response_digest'], 'query': review['query_context']}
            if response_kind != 'list':identity_fields['response_kind'] = response_kind
            identity = json.dumps(identity_fields,
                                  sort_keys=True, separators=(',', ':'), ensure_ascii=True)
            digest = hashlib.sha256(identity.encode()).hexdigest()
            prior = db.execute('SELECT review_json FROM supplied_readback_reviews WHERE attempt_id=? AND evidence_digest=?',
                               (attempt_id, digest)).fetchone()
            if prior:
                return json.loads(prior[0])
            review.update(attempt_id=attempt_id, evidence_digest=digest)
            db.execute('INSERT INTO supplied_readback_reviews VALUES (?,?,?,?)',
                       (attempt_id, digest, review['checked_at'], json.dumps(review, ensure_ascii=False, allow_nan=False)))
            return review


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _post_contract(contract, body, token, timeout):
    # Fixed official origin/path, no caller-supplied endpoint or proxy redirect.
    request = urllib.request.Request(BASE_URL+CONTRACTS[contract]['path'], data=body.encode('utf-8'),
                                     headers={'Access-Token': token, 'Content-Type': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        payload = response.read(1024*1024+1)
        if len(payload) > 1024*1024:
            raise ValueError("响应超过大小限制")
        from .plan_readback import _pairs, _invalid_constant
        return json.loads(payload, object_pairs_hook=_pairs, parse_constant=_invalid_constant)


def _post_budget(body, token, timeout):
    return _post_contract('budget', body, token, timeout)


def _post_status(body, token, timeout):
    return _post_contract('status', body, token, timeout)


def _post_roi(body, token, timeout):
    return _post_contract('roi', body, token, timeout)


def _post_control_budget(body, token, timeout):
    return _post_contract('control_budget', body, token, timeout)


def _post_control_status(body, token, timeout):
    return _post_contract('control_status', body, token, timeout)


class _PlanClient:
    def __init__(self, ledger, *, enable_network=False, transport=None, clock=None):
        if transport is not None and not callable(transport):
            raise ValueError('提交传输器必须是显式可调用对象')
        if clock is not None and not callable(clock):
            raise ValueError('提交时钟必须是显式可调用对象')
        self.ledger = ledger
        self.enable_network = enable_network is True
        self.transport = self.default_transport if transport is None else transport
        self.clock = now_cn if clock is None else clock

    def dispatch(self, attempt_id, proposal_id, snapshots, settings, permit, token_provider, now=None, *, events=(), migrations=(), recovery_snapshots=None, recovery_events=None):
        # Check this before credentials, evaluation, ledger claims or transport.
        if not self.enable_network:
            raise PermissionError("计划提交适配器未启用；当前观察服务没有提交入口")
        wall_started = _clock(self.clock())
        monotonic_started = time.monotonic()
        # The caller's evaluation timestamp cannot override the live clock.
        # Isolated replay tests must explicitly inject their own clock instead.
        if now is not None:
            _clock(now)
        began = wall_started
        previous_wall, previous_monotonic = wall_started, monotonic_started
        if (not isinstance(permit, self.permit_type) or not isinstance(permit.issued_at, datetime)
                or not isinstance(permit.expires_at, datetime) or permit.issued_at.tzinfo is None
                or permit.expires_at.tzinfo is None or not permit.issued_at <= began < permit.expires_at
                or not timedelta(0) < permit.expires_at-permit.issued_at <= timedelta(minutes=5)
                or not isinstance(permit.authorization_reference, str) or not permit.authorization_reference.strip()):
            raise PermissionError("需要本次具体请求的有效授权记录；不能由配置或AI授予")
        if not isinstance(attempt_id, str) or not 1 <= len(attempt_id) <= 150 or not attempt_id.isascii() or not all(c.isalnum() or c in '_-' for c in attempt_id):
            raise ValueError("提交编号无效")
        # Retain live list containers; exhaust a one-shot iterable only once.
        def materialize(value):
            return value if isinstance(value, list) else list(value)
        snapshots, events, migrations = map(materialize, (snapshots, events, migrations))
        recovery_snapshots = None if recovery_snapshots is None else materialize(recovery_snapshots)
        recovery_events = None if recovery_events is None else materialize(recovery_events)

        def checkpoint():
            nonlocal previous_wall, previous_monotonic
            wall, monotonic = _clock(self.clock()), time.monotonic()
            if wall < previous_wall or monotonic < previous_monotonic:
                raise PermissionError('提交时钟倒退，不能沿用旧授权')
            previous_wall, previous_monotonic = wall, monotonic
            at = max(wall, began+timedelta(seconds=monotonic-monotonic_started))
            if not permit.issued_at <= at < permit.expires_at:
                raise PermissionError('提交授权已过期，请重新审阅具体请求')
            return at

        def reviewed(at):
            rows, cfg, ev, mi, rr, re = copy.deepcopy((snapshots, settings, events, migrations, recovery_snapshots, recovery_events))
            evaluation = evaluate_all(rows, cfg, at, events=ev, migrations=mi, recovery_snapshots=rr, recovery_events=re)
            proposal = next((p for p in evaluation['recommendations'] if p['id'] == proposal_id), None)
            if proposal is None or proposal.get('action') not in self.actions or proposal['eligible'] is not True:
                raise ValueError('当前完整规则链路没有对应的有效计划建议')
            account = next((a for a in cfg.get('accounts', []) if a.get('id') == proposal['account_id']), None)
            if account is None or account.get('observe', True) is not True:
                raise ValueError('提交账户已撤出当前观察范围')
            entity = next((e for e in evaluation['entities'] if e['account_id'] == proposal['account_id']
                           and e['entity_id'] == proposal['entity_id'] and e['scope'] == proposal['scope']
                           and e['source_key'] == proposal['source_key']), None)
            snapshot = next((s for s in rows if entity and s.get('id') == entity['latest_snapshot_id']), None)
            if snapshot is None:
                raise ValueError('缺少计划建议对应的最新快照')
            preview = prepare_action(proposal, snapshot, cfg, at, snapshots=rows, evaluation=evaluation)
            if not preview['supported_for_preview']:
                raise ValueError('当前数据未满足对应官方合同的准备条件')
            if permit.account_id != proposal['account_id'] or permit.request_digest != preview['digest']:
                raise PermissionError('授权账户或请求摘要不匹配，请重新审阅具体请求')
            def related(values):
                return None if values is None else [v for v in values if v.get('account_id') in {None, proposal['account_id']}]
            basis = (proposal, account, cfg.get('rules', {}), related(rows), related(ev), related(mi), related(rr), related(re))
            qualification = hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=True,
                allow_nan=False, separators=(',', ':')).encode()).hexdigest()
            return proposal, snapshot, preview, cfg, qualification

        proposal, snapshot, preview, cfg, qualification = reviewed(began)
        # The provider is an explicit dependency; no browser or environment read.
        try:
            token = token_provider()
        except Exception:
            raise ValueError('显式凭据提供器未返回可用凭据') from None
        if not isinstance(token, str) or not 1 <= len(token) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError("开放平台凭据格式无效")
        at = checkpoint()
        proposal, snapshot, preview, cfg, current_qualification = reviewed(at)
        if current_qualification != qualification:
            raise ValueError('读取凭据期间提交依据已变化，请重新评估')
        self.ledger.claim(attempt_id, preview['digest'], at, {
            'account_id': proposal['account_id'], 'entity_id': proposal['entity_id'], 'proposal_id': proposal_id,
            'action': proposal['action'], 'before': proposal['before'], 'after': proposal['after'],
            'scope': proposal['scope'], 'parent_plan_id': snapshot.get('context',{}).get('parent_plan_id') if proposal['scope']=='control' else None,
            'control_type': snapshot.get('context',{}).get('control_type') if proposal['scope']=='control' else None,
            'metric_basis': proposal['metric_basis'], 'rule': proposal['rule'],
            'snapshot_id': snapshot['id'], 'data_at': snapshot['source']['data_at'],
            'marketing_goal': {'live': 'LIVE_PROM_GOODS', 'product': 'VIDEO_PROM_GOODS'}.get(snapshot.get('scene')),
            'marketing_goal_basis': 'normalized_snapshot_scene',
            'plan_optimization_goal': snapshot.get('context', {}).get('plan_optimization_goal'),
            'plan_delivery_mode': snapshot.get('context', {}).get('plan_delivery_mode'),
            'contract': self.contract, 'cooldown_seconds': max(3600, _settings(cfg)[0]['cooldown_minutes']*60),
            'qualification_digest': qualification,
            'authorization_reference_digest': hashlib.sha256(permit.authorization_reference.encode()).hexdigest()})
        try:
            _, _, _, _, final_qualification = reviewed(checkpoint())
            if final_qualification != qualification:
                raise ValueError('等待SQLite期间提交依据已变化')
            checkpoint()
        except Exception:
            result = {'attempt_id': attempt_id, 'submitted': False, 'state': 'failed_before_io',
                      'business_success': False, 'platform_state_observed': False, 'automatic_retry': False,
                      'note': '动作前复核未通过，没有发送请求；本次编号和摘要保留审计'}
            self.ledger.finish(attempt_id, 'failed', result)
            return result
        try:
            response = self.transport(preview['request']['body_json'], token, 10)
            decoded = self.decode_response(response, [proposal['entity_id']])
            state = 'confirmed' if decoded['success'] else 'unknown' if decoded['unknown'] else 'failed'
            result = {'attempt_id': attempt_id, 'submitted': True, 'state': state,
                      'business_success': decoded['success'], 'confirmed_ids': decoded['confirmed'],
                      'failed_ids': decoded['failed'], 'unknown_ids': decoded['unknown'],
                      'platform_state_observed': False, 'automatic_retry': False}
        except Exception:
            # Never retain raw response/error text; either may echo credentials.
            state = 'unknown'
            result = {'attempt_id': attempt_id, 'submitted': True, 'state': state,
                      'business_success': False, 'platform_state_observed': False,
                      'automatic_retry': False, 'note': '提交结果不明确；需独立报表对账，不自动重试'}
        self.ledger.finish(attempt_id, state, result)
        return result


class BudgetClient(_PlanClient):
    actions = frozenset({'budget_up', 'budget_down'})
    permit_type = BudgetPermit
    contract = 'budget'
    default_transport = staticmethod(_post_budget)
    decode_response = staticmethod(interpret_budget_response)


class PlanStatusClient(_PlanClient):
    actions = frozenset({'pause_plan', 'resume_plan'})
    permit_type = PlanStatusPermit
    contract = 'status'
    default_transport = staticmethod(_post_status)
    decode_response = staticmethod(interpret_status_response)


class RoiTargetClient(_PlanClient):
    actions = frozenset({'roi_target_up', 'roi_target_down'})
    permit_type = RoiTargetPermit
    contract = 'roi'
    default_transport = staticmethod(_post_roi)
    decode_response = staticmethod(interpret_roi_response)


class ControlBudgetClient(_PlanClient):
    actions = frozenset({'control_budget_up'})
    permit_type = ControlBudgetPermit
    contract = 'control_budget'
    default_transport = staticmethod(_post_control_budget)
    decode_response = staticmethod(interpret_control_budget_response)


class ControlStatusClient(_PlanClient):
    actions = frozenset({'pause_control'})
    permit_type = ControlStatusPermit
    contract = 'control_status'
    default_transport = staticmethod(_post_control_status)
    decode_response = staticmethod(interpret_control_status_response)
