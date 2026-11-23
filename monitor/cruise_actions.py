"""Persistent, per-action approvals for original-cruise control tasks.

Task budget/duration are separate from main-plan settings. This module performs
no browser or platform IO. Read-only metrics never become approval bindings.
"""
from decimal import Decimal
import json
import math
import re
import secrets
import sqlite3
from pathlib import Path

from .browser_actions import (BrowserActions, FRESH_SECONDS, APPROVAL_SECONDS,
                              RECONCILED, _dump, _hash, _id, _number, _shape, _time)
from datetime import timedelta

PROTOCOL = 1
TASK_COOLDOWN_SECONDS = 15 * 60
ACTIONS = {
    'control_budget_up': ('budget', '增加调控任务预算'),
    'control_duration_up': ('duration_minutes', '增加调控任务时长'),
    'control_pause': ('running', '暂停调控任务'),
    'control_stop': ('status', '结束调控任务'),
}
REQUIRED_SURFACE = set(('target_kind account_id plan_id task_id path group name budget '
                        'duration_minutes running status actions captured_at tab_id document_id').split())
OBSERVATION_FIELDS = {'metrics', 'context', 'metric_basis'}
STATUSES = {'调控中', '已暂停', '调控结束'}


def _decimal(value, *, zero=False):
    if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d{1,2})?', value) or len(value) > 24:
        raise ValueError('任务设置必须是最多两位小数的十进制字符串')
    if zero and Decimal(value) == 0:
        return '0.00'
    return _number(value)


def _observation(value, field):
    if not isinstance(value, dict) or len(value) > 50:
        raise ValueError('任务只读观测字段无效')
    # Bound informational JSON and reject NaN/Infinity without interpreting it
    # as configuration, qualification, or a successful platform operation.
    try:
        if len(_dump(value)) > 10000:
            raise ValueError('任务只读观测过长')
    except (TypeError, OverflowError):
        raise ValueError('任务只读观测不是有效JSON') from None
    if field == 'metrics':
        if set(value) - {'spend', 'comprehensive_cost', 'roi', 'orders'}:
            raise ValueError('任务指标名称无效')
        for key, number in value.items():
            if number is None:
                continue
            if type(number) not in (int, float) or not math.isfinite(number) or not 0 <= number <= 10**12:
                raise ValueError('任务指标数值无效')
            if key == 'orders' and number != int(number):
                raise ValueError('任务订单数须为整数')
    return value


def normalize_task_surface(value, accounts):
    if not isinstance(value, dict) or not REQUIRED_SURFACE <= set(value) or set(value) - REQUIRED_SURFACE - OBSERVATION_FIELDS:
        raise ValueError('调控任务页面字段不匹配')
    out = dict(value)
    if out['target_kind'] != 'control_task':
        raise ValueError('任务队列不能接受主计划设置')
    for field in ('account_id', 'plan_id', 'task_id'):
        _id(out[field])
    _id(out['document_id'], opaque=True)
    account = next((a for a in accounts if a.get('id') == out['account_id'] and a.get('observe', True)), None)
    if account is None or out['group'] != account.get('tab_group'):
        raise ValueError('任务不在当前账户与Chrome分组内')
    expected = {'live': {'/uni-prom/detail'}, 'product': {'/overall-prom'}}
    if out['path'] not in expected.get(account.get('scene'), set()):
        raise ValueError('任务页面与当前账户场景不符')
    if type(out['tab_id']) is not int or out['tab_id'] <= 0:
        raise ValueError('任务缺少明确Chrome标签ID')
    if not isinstance(out['name'], str) or not 1 <= len(out['name']) <= 200 or any(ord(c) < 32 for c in out['name']):
        raise ValueError('任务名称无效')
    if out['budget'] is not None:
        out['budget'] = _decimal(out['budget'], zero=True)
    if out['duration_minutes'] is not None:
        out['duration_minutes'] = _decimal(out['duration_minutes'])
    if out['running'] is not None and type(out['running']) is not bool:
        raise ValueError('任务运行状态须明确或缺失')
    if out['status'] is not None and (not isinstance(out['status'], str) or out['status'] not in STATUSES):
        raise ValueError('任务状态未归一为明确原生状态')
    if out['status'] is not None and out['running'] is not (out['status'] == '调控中'):
        raise ValueError('任务运行开关与状态不一致')
    actions = out['actions']
    if not isinstance(actions, list) or any(not isinstance(a, str) for a in actions) or len(actions) != len(set(actions)) or set(actions) - ACTIONS.keys():
        raise ValueError('任务动作能力无效')
    for action in actions:
        if out[ACTIONS[action][0]] is None:
            raise ValueError('任务动作缺少明确的原设置')
        if action == 'control_pause' and out['running'] is not True:
            raise ValueError('暂停能力仅适用于明确在调控的任务')
        if action == 'control_stop' and out['status'] not in {'调控中', '已暂停'}:
            raise ValueError('结束能力仅适用于尚未结束的任务')
    out['actions'] = sorted(actions)
    for field in ('metrics', 'context'):
        if field in out:
            out[field] = _observation(out[field], field)
    if 'metric_basis' in out and out['metric_basis'] not in {'control_payment', 'unknown'}:
        raise ValueError('任务观测ROI口径无效')
    _time(out['captured_at'])
    return out


def task_binding(surface):
    bound={k: v for k, v in surface.items() if k not in OBSERVATION_FIELDS | {'captured_at'}}
    date=surface.get('context',{}).get('metric_date')
    if date is not None:bound['context']={'metric_date':date}
    return bound


class CruiseActions(BrowserActions):
    """Own database; shared SQL primitives never merge task and main-plan IDs."""

    def __init__(self, path, clock=None, action_validator=None):
        super().__init__(path, clock)
        self.action_validator = action_validator

    def observe(self, payload, policy):
        _shape(payload, 'protocol worker_id epoch armed revision surfaces')
        if (type(payload['protocol']) is not int or payload['protocol'] != PROTOCOL
                or type(payload['armed']) is not bool or payload['revision'] != policy['revision']):
            raise ValueError('任务执行协议或范围版本不匹配')
        worker_id = _id(payload['worker_id'], opaque=True); epoch = _id(payload['epoch'], opaque=True)
        if not isinstance(payload['surfaces'], list) or len(payload['surfaces']) > 1000:
            raise ValueError('任务页面清单无效')
        surfaces = [normalize_task_surface(s, policy['accounts']) for s in payload['surfaces']]
        keys = [(s['account_id'], s['plan_id'], s['task_id']) for s in surfaces]
        if len(keys) != len(set(keys)):
            raise ValueError('同一执行器不能上报重复的父计划/任务')
        now = self._now()
        if any(not 0 <= (now - _time(s['captured_at'])).total_seconds() <= FRESH_SECONDS for s in surfaces):
            raise ValueError('任务页面观察已过期')
        with self._tx() as db:
            self._sweep(db)
            old = db.execute('SELECT * FROM browser_workers WHERE id=?', (worker_id,)).fetchone()
            if old and (old['epoch'] != epoch or not payload['armed']):
                rows = db.execute("SELECT id,state FROM browser_actions WHERE worker_id=? AND state IN ('draft','approved','claimed','submitting')", (worker_id,)).fetchall()
                for row in rows:
                    state = 'cancelled' if row['state'] in {'draft', 'approved'} else 'unknown'
                    db.execute('UPDATE browser_actions SET state=? WHERE id=?', (state, row['id']))
                    self._event(db, row['id'], state, {'reason': '任务执行开关或会话变化'})
            db.execute('INSERT OR REPLACE INTO browser_workers VALUES(?,?,?,?,?,?)',
                       (worker_id, epoch, int(payload['armed']), policy['revision'], now.isoformat(), _dump(surfaces)))
        return {'ok': True, 'protocol': PROTOCOL}

    def _task_surface(self, db, worker_id, epoch, revision, account_id, plan_id, task_id):
        worker = db.execute('SELECT * FROM browser_workers WHERE id=?', (worker_id,)).fetchone()
        now = self._now()
        if (not worker or not worker['armed'] or worker['epoch'] != epoch or worker['revision'] != revision
                or not 0 <= (now - _time(worker['seen_at'])).total_seconds() <= FRESH_SECONDS):
            raise ValueError('任务执行器未开启、会话变化或通信过期')
        surface = next((s for s in json.loads(worker['surfaces']) if s['account_id'] == account_id
                        and s['plan_id'] == plan_id and s['task_id'] == task_id), None)
        if not surface or not 0 <= (now - _time(surface['captured_at'])).total_seconds() <= FRESH_SECONDS:
            raise ValueError('缺少明确父计划下该任务的当前观察')
        return surface

    def create(self, payload, policy):
        _shape(payload, 'worker_id epoch account_id plan_id task_id action after reason')
        action = payload['action']
        if not isinstance(action, str) or action not in ACTIONS:
            raise ValueError('不支持该调控任务动作')
        if not isinstance(payload['reason'], str) or not 2 <= len(payload['reason'].strip()) <= 500:
            raise ValueError('请填写这条任务动作的具体原因')
        with self._tx() as db:
            self._sweep(db)
            surface = normalize_task_surface(self._task_surface(db, payload['worker_id'], payload['epoch'], policy['revision'],
                payload['account_id'], payload['plan_id'], payload['task_id']), policy['accounts'])
            if action not in surface['actions']:
                raise ValueError('任务页面未明确识别这个动作入口')
            field = ACTIONS[action][0]; before = surface[field]
            if action in {'control_budget_up', 'control_duration_up'}:
                after = _decimal(payload['after'])
                if Decimal(after) <= Decimal(before):
                    raise ValueError('任务预算或时长须明确上调到更大的目标值')
                if action == 'control_duration_up' and Decimal(after) % Decimal(30) != 0:
                    raise ValueError('目标任务时长须为30分钟的整数倍')
            elif action == 'control_pause':
                if before is not True or payload['after'] is not False:
                    raise ValueError('任务暂停只能从明确在调控变为暂停')
                after = False
            else:
                if before not in {'调控中', '已暂停'} or payload['after'] != '调控结束':
                    raise ValueError('任务结束须明确从未结束变为调控结束')
                after = '调控结束'
            for old in db.execute('SELECT a.state,a.claimed_at,a.intent,r.resolved_at FROM browser_actions a LEFT JOIN browser_action_resolutions r ON r.action_id=a.id').fetchall():
                intent = json.loads(old['intent'])
                if any(intent[k] != surface[k] for k in ('account_id', 'plan_id', 'task_id')):
                    continue
                if old['state'] in {'draft', 'approved', 'claimed', 'submitting', 'unknown'}:
                    raise ValueError('此任务已有待处理或结果不明的调控单')
                if old['state'] == 'verified' and intent['action'] == action and old['claimed_at'] and (self._now() - _time(old['claimed_at'])).total_seconds() < TASK_COOLDOWN_SECONDS:
                    raise ValueError('此任务距离上一次已核验调整不足15分钟')
                if old['state'] in RECONCILED and intent['action'] == action and (not old['resolved_at'] or (self._now()-_time(old['resolved_at'])).total_seconds()<TASK_COOLDOWN_SECONDS):
                    raise ValueError('此任务同动作距离结果核对不足15分钟')
            now = self._now(); action_id = secrets.token_hex(16)
            intent = {'target_kind': 'control_task', 'account_id': surface['account_id'], 'plan_id': surface['plan_id'],
                'task_id': surface['task_id'], 'task_name': surface['name'], 'action': action, 'action_label': ACTIONS[action][1],
                'field': field, 'before': before, 'after': after, 'reason': payload['reason'].strip(),
                'surface': task_binding(surface), 'origin': 'manual_review',
                'note': '原巡航调控任务动作；不是主计划预算或ROI。只读指标不认证规则资格。'}
            digest = _hash(intent); expires = (now + timedelta(seconds=APPROVAL_SECONDS)).isoformat()
            db.execute('INSERT INTO browser_actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (action_id, 'draft', now.isoformat(), expires, payload['worker_id'], payload['epoch'], policy['revision'],
                 _dump(intent), digest, None, None, None))
            self._event(db, action_id, 'draft', {'intent_digest': digest})
            return self._public(db.execute('SELECT * FROM browser_actions WHERE id=?', (action_id,)).fetchone())

    def _check(self, db, row, policy):
        if row['revision'] != policy['revision']:
            raise ValueError('任务采集范围已变化，请重新准备')
        intent = json.loads(row['intent'])
        surface = normalize_task_surface(self._task_surface(db, row['worker_id'], row['epoch'], policy['revision'],
            intent['account_id'], intent['plan_id'], intent['task_id']), policy['accounts'])
        if task_binding(surface) != intent['surface']:
            raise ValueError('任务页面、能力、父计划或当前设置已变化，请重新准备')
        if self.action_validator is not None:
            self.action_validator(row['id'], intent)
        return intent

    def begin(self, payload, policy):
        _shape(payload, 'id lease observation')
        with self._tx() as db:
            self._sweep(db)
            row = db.execute('SELECT * FROM browser_actions WHERE id=?', (payload['id'],)).fetchone()
            if not row or row['state'] != 'claimed' or not secrets.compare_digest(row['lease'] or '', str(payload['lease'])):
                raise ValueError('任务动作未占用或一次提交许可已消费')
            if _time(row['expires_at']) <= self._now():
                raise ValueError('任务审批有效期已结束')
            intent = self._check(db, row, policy)
            observation = normalize_task_surface(payload['observation'], policy['accounts'])
            age = (self._now() - _time(observation['captured_at'])).total_seconds()
            if not 0 <= age <= 10 or task_binding(observation) != intent['surface']:
                raise ValueError('任务提交前核对不匹配或已过期')
            db.execute("UPDATE browser_actions SET state='submitting' WHERE id=?", (row['id'],))
            self._event(db, row['id'], 'submitting')
            return {'ok': True, 'id': row['id'], 'submit_once': True}

    def receipt(self, payload):
        _shape(payload, 'id lease submitted observation message')
        if type(payload['submitted']) is not bool or not isinstance(payload['message'], str) or len(payload['message']) > 500:
            raise ValueError('任务回执格式无效')
        with self._tx() as db:
            self._sweep(db)
            row = db.execute('SELECT * FROM browser_actions WHERE id=?', (payload['id'],)).fetchone()
            if not row or not secrets.compare_digest(row['lease'] or '', str(payload['lease'])):
                raise ValueError('回执不属于这条任务动作')
            receipt = {k: v for k, v in payload.items() if k != 'lease'}
            if row['receipt']:
                if json.loads(row['receipt']) != receipt:
                    raise ValueError('同一任务动作已收到不同回执')
                return {'ok': True, 'id': row['id'], 'state': row['state']}
            if row['state'] in RECONCILED:
                db.execute('UPDATE browser_actions SET receipt=? WHERE id=?',(_dump(receipt),row['id']))
                self._event(db,row['id'],'late_receipt',{'message':payload['message'],'note':'核对后回执仅留存，不重试或改变核对结果'})
                return {'ok':True,'id':row['id'],'state':row['state'],'late':True}
            if row['state'] not in {'claimed', 'submitting', 'unknown'}:
                raise ValueError('任务动作状态不接受回执')
            submitted_at = db.execute("SELECT at FROM browser_action_journal WHERE action_id=? AND event='submitting' ORDER BY seq DESC LIMIT 1", (row['id'],)).fetchone()
            if payload['submitted'] and submitted_at is None:
                raise ValueError('任务未取得一次提交许可')
            state = 'unknown' if payload['submitted'] or row['state'] in {'submitting', 'unknown'} else 'rejected'
            intent = json.loads(row['intent']); observation = payload['observation']
            if payload['submitted'] and isinstance(observation, dict):
                _shape(observation, 'account_id plan_id task_id tab_id document_id readback_document_id field value captured_at reloaded')
                match = all(observation[k] == intent['surface'][k] for k in ('account_id', 'plan_id', 'task_id', 'tab_id', 'document_id'))
                age = (self._now() - _time(observation['captured_at'])).total_seconds()
                readback_document = _id(observation['readback_document_id'], opaque=True)
                if (match and observation['field'] == intent['field'] and observation['value'] == intent['after']
                        and type(observation['value']) is type(intent['after'])
                        and submitted_at is not None and _time(observation['captured_at']) >= _time(submitted_at['at'])
                        and readback_document != observation['document_id'] and 0 <= age <= 10 and observation['reloaded'] is True):
                    state = 'verified'
            db.execute('UPDATE browser_actions SET state=?,receipt=? WHERE id=?', (state, _dump(receipt), row['id']))
            self._event(db, row['id'], state, {'submitted': payload['submitted'], 'message': payload['message'],
                'verification': '刷新后任务字段匹配；不认证投放效果或变化因果' if state == 'verified' else '任务最终设置尚未核验'})
            return {'ok': True, 'id': row['id'], 'state': state}

    def _reconciliation_evidence(self, db, row, policy, worker_id, epoch):
        if not row or row['state']!='unknown' or not row['claimed_at']:
            raise ValueError('仅可核对已占用且结果不明的任务动作')
        if worker_id!=row['worker_id']:raise ValueError('必须由原执行器重新观察任务')
        intent=json.loads(row['intent'])
        current=normalize_task_surface(self._task_surface(db,worker_id,epoch,policy['revision'],
            intent['account_id'],intent['plan_id'],intent['task_id']),policy['accounts'])
        original=intent['surface']
        if any(current[k]!=original[k] for k in ('tab_id','path','group')):
            raise ValueError('请回到原标签同一父计划和任务进行核对')
        if current['document_id']==original['document_id'] or _time(current['captured_at'])<=_time(row['claimed_at']):
            raise ValueError('请先刷新原任务页面，等待新文档当前设置上报')
        value=current[intent['field']]
        if value is None or type(value) is not type(intent['after']):
            raise ValueError('缺少明确任务当前设置，保持结果不明')
        match='target' if value==intent['after'] else 'before' if value==intent['before'] else 'other'
        return {'revision':policy['revision'],'worker_id':worker_id,'epoch':epoch,
            'intent_digest':row['digest'],'surface':current,'match':match,'value':value}

    @staticmethod
    def _reconciliation_binding(evidence):
        return {**evidence,'surface':task_binding(evidence['surface'])}

    def has_active_or_unknown(self):
        with self._tx() as db:
            self._sweep(db)
            return db.execute("SELECT 1 FROM browser_actions WHERE state IN ('claimed','submitting','unknown') LIMIT 1").fetchone() is not None

    def history(self, account_id):
        """Read-only SELECT so a rule validator can call this inside _check."""
        _id(account_id)
        db = sqlite3.connect(Path(self.path).resolve().as_uri() + '?mode=ro', timeout=10, uri=True)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute('SELECT a.id,a.state,a.claimed_at,a.created_at,a.intent,r.resolved_at FROM browser_actions a LEFT JOIN browser_action_resolutions r ON r.action_id=a.id ORDER BY a.created_at,a.id').fetchall()
            result = []
            for row in rows:
                intent = json.loads(row['intent'])
                if intent['account_id'] == account_id:
                    result.append({'id': row['id'], 'account_id': account_id, 'entity_id': intent['task_id'],
                        'parent_plan_id': intent['plan_id'], 'action': intent['action'], 'state': row['state'],
                        'at': row['resolved_at'] or row['claimed_at'] or row['created_at']})
            return result
        finally:
            db.close()

    def status(self, policy):
        with self._tx() as db:
            self._sweep(db); now = self._now(); workers = []
            for row in db.execute('SELECT * FROM browser_workers ORDER BY seen_at DESC').fetchall():
                current = row['revision'] == policy['revision'] and 0 <= (now - _time(row['seen_at'])).total_seconds() <= FRESH_SECONDS
                surfaces = []
                if current:
                    for s in json.loads(row['surfaces']):
                        try:
                            s = normalize_task_surface(s, policy['accounts'])
                            if 0 <= (now - _time(s['captured_at'])).total_seconds() <= FRESH_SECONDS:
                                surfaces.append(s)
                        except ValueError:
                            continue
                workers.append({'worker_id': row['id'], 'epoch': row['epoch'], 'armed': bool(row['armed']) and current,
                    'current': current, 'seen_at': row['seen_at'], 'surfaces': surfaces})
            actions = [self._public(row,db.execute('SELECT * FROM browser_action_resolutions WHERE action_id=?',(row['id'],)).fetchone())
                for row in db.execute('SELECT * FROM browser_actions ORDER BY created_at DESC,id DESC LIMIT 100').fetchall()]
            return {'ok': True, 'protocol': PROTOCOL, 'mode': 'per_action_confirmation', 'target_kind': 'control_task',
                'workers': workers, 'actions': actions,
                'execution_available': any(w['armed'] and any(s['actions'] for s in w['surfaces']) for w in workers),
                'note': '原巡航调控任务逐条确认；任务与主计划独立，结果不明不重试。'}
