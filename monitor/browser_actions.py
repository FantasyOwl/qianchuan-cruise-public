"""Single-action approval queue for the browser executor.

No browser or platform IO here. Imported reports and rule suggestions cannot
become commands. Only a fresh, armed browser worker can advertise controls;
the local UI then approves a concrete immutable change. A click receipt is
never sufficient to mark the change verified.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import secrets
import sqlite3
from pathlib import Path

PROTOCOL = 1
FRESH_SECONDS = 90
APPROVAL_SECONDS = 120
CLAIM_SECONDS = 90
RECONCILE_SECONDS = 60
ACTIONS = {
    'budget_up': ('budget', '上调主计划日预算'),
    'budget_down': ('budget', '下调主计划日预算'),
    'roi_target_up': ('target_roi', '上调主计划ROI设置'),
    'roi_target_down': ('target_roi', '下调主计划ROI设置'),
    'pause_plan': ('running', '暂停主计划'),
}
RECONCILED = {'reconciled_target', 'reconciled_before', 'reconciled_other'}
FINAL = {'verified', 'rejected', 'unknown', 'cancelled', 'expired'} | RECONCILED


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return hashlib.sha256(_dump(value).encode()).hexdigest()


def _time(value):
    if not isinstance(value, str):
        raise ValueError('时间必须为带时区的ISO字符串')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('时间格式无效') from None
    if parsed.tzinfo is None:
        raise ValueError('时间缺少时区')
    return parsed.astimezone(timezone.utc)


def _id(value, *, opaque=False):
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ValueError('标识格式无效')
    if opaque:
        if not all(c.isascii() and (c.isalnum() or c in '_-') for c in value):
            raise ValueError('执行器标识格式无效')
    elif not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**64:
        raise ValueError('账户或计划ID无效')
    return value


def _number(value):
    if not isinstance(value, str) or len(value) > 24:
        raise ValueError('变更值必须是十进制字符串')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError('数值格式无效') from None
    if not result.is_finite() or not 0 < result <= Decimal('1000000000000') or result != result.quantize(Decimal('.01')):
        raise ValueError('数值须为正数且最多两位小数')
    return format(result, '.2f')


def _shape(value, required):
    if not isinstance(value, dict) or set(value) != set(required.split()):
        raise ValueError('调控协议字段不匹配')


def normalize_surface(value, accounts):
    _shape(value, 'account_id plan_id tab_id document_id path group name budget target_roi roi_basis running actions captured_at')
    out = dict(value)
    _id(out['account_id']); _id(out['plan_id']); _id(out['document_id'], opaque=True)
    account = next((a for a in accounts if a.get('id') == out['account_id'] and a.get('observe', True)), None)
    if account is None or out['group'] != account.get('tab_group'):
        raise ValueError('执行对象不在当前账户和Chrome分组内')
    if type(out['tab_id']) is not int or out['tab_id'] <= 0:
        raise ValueError('缺少明确Chrome标签ID')
    expected = {'live': {'/uni-prom', '/uni-prom/detail'}, 'product': {'/overall-prom'}}
    if out['path'] not in expected.get(account.get('scene'), set()):
        raise ValueError('执行页面与账户场景不匹配')
    if not isinstance(out['name'], str) or not 1 <= len(out['name']) <= 200 or any(ord(c) < 32 for c in out['name']):
        raise ValueError('计划名称无效')
    for key in ('budget', 'target_roi'):
        if out[key] is not None:
            out[key] = _number(out[key])
    if out['roi_basis'] not in {'overall_payment', 'net', 'marketing', 'unknown'}:
        raise ValueError('计划ROI设置口径无效')
    if out['running'] is not None and type(out['running']) is not bool:
        raise ValueError('计划投放状态无效')
    if not isinstance(out['actions'], list) or len(out['actions']) != len(set(out['actions'])) or set(out['actions']) - ACTIONS.keys():
        raise ValueError('浏览器动作能力无效')
    out['actions'] = sorted(out['actions'])
    for action in out['actions']:
        field = ACTIONS[action][0]
        if out[field] is None or (field == 'target_roi' and out['roi_basis'] == 'unknown'):
            raise ValueError('可执行动作缺少明确当前值或口径')
    _time(out['captured_at'])
    return out


def surface_binding(surface):
    # Capture time is freshness evidence, not the identity of an unchanged setting.
    return {k: v for k, v in surface.items() if k != 'captured_at'}


def recommendation_binding(proposal):
    return {key: proposal.get(key) for key in ('id', 'account_id', 'entity_id', 'scope', 'action',
            'before', 'after', 'metric_basis', 'source_key', 'mode', 'eligible', 'parameters')}


class BrowserActions:
    def __init__(self, path, clock=None, recommendation_validator=None, action_validator=None):
        self.path = str(path)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.recommendation_validator = recommendation_validator
        self.action_validator = action_validator
        with self._tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS browser_workers(id TEXT PRIMARY KEY, epoch TEXT, armed INTEGER, revision TEXT, seen_at TEXT, surfaces TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS browser_actions(id TEXT PRIMARY KEY, state TEXT, created_at TEXT, expires_at TEXT, worker_id TEXT, epoch TEXT, revision TEXT, intent TEXT, digest TEXT, claimed_at TEXT, lease TEXT, receipt TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS browser_action_journal(seq INTEGER PRIMARY KEY, action_id TEXT, at TEXT, event TEXT, detail TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS browser_action_resolutions(action_id TEXT PRIMARY KEY, digest TEXT, created_at TEXT, expires_at TEXT, evidence TEXT, resolved_at TEXT)')

    def _now(self):
        return _time(self.clock().isoformat())

    @contextmanager
    def _tx(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _event(self, db, action_id, name, detail=None):
        db.execute('INSERT INTO browser_action_journal(action_id,at,event,detail) VALUES(?,?,?,?)',
                   (action_id, self._now().isoformat(), name, _dump(detail or {})))

    def _sweep(self, db):
        now = self._now()
        for row in db.execute("SELECT * FROM browser_actions WHERE state IN ('draft','approved','claimed','submitting')").fetchall():
            if row['state'] in {'draft', 'approved'} and _time(row['expires_at']) <= now:
                state = 'expired'
            elif row['claimed_at'] and (now - _time(row['claimed_at'])).total_seconds() > CLAIM_SECONDS:
                state = 'unknown'
            else:
                continue
            db.execute('UPDATE browser_actions SET state=? WHERE id=?', (state, row['id']))
            self._event(db, row['id'], state, {'reason': '审批过期' if state == 'expired' else '执行回执未到，不自动重试'})

    def observe(self, payload, policy):
        _shape(payload, 'protocol worker_id epoch armed revision surfaces')
        if payload['protocol'] != PROTOCOL or type(payload['armed']) is not bool or payload['revision'] != policy['revision']:
            raise ValueError('浏览器执行协议或当前范围版本不匹配')
        worker_id = _id(payload['worker_id'], opaque=True); epoch = _id(payload['epoch'], opaque=True)
        if not isinstance(payload['surfaces'], list) or len(payload['surfaces']) > 1000:
            raise ValueError('执行对象清单无效')
        surfaces = [normalize_surface(s, policy['accounts']) for s in payload['surfaces']]
        keys = [(s['account_id'], s['plan_id']) for s in surfaces]
        if len(keys) != len(set(keys)):
            raise ValueError('同一执行器不能上报重复计划')
        now = self._now()
        if any(not 0 <= (now - _time(s['captured_at'])).total_seconds() <= FRESH_SECONDS for s in surfaces):
            raise ValueError('页面配置观察时间已失效')
        with self._tx() as db:
            self._sweep(db)
            old = db.execute('SELECT * FROM browser_workers WHERE id=?', (worker_id,)).fetchone()
            if old and (old['epoch'] != epoch or not payload['armed']):
                rows = db.execute("SELECT id,state FROM browser_actions WHERE worker_id=? AND state IN ('draft','approved','claimed','submitting')", (worker_id,)).fetchall()
                for row in rows:
                    state = 'cancelled' if row['state'] in {'draft', 'approved'} else 'unknown'
                    db.execute('UPDATE browser_actions SET state=? WHERE id=?', (state, row['id']))
                    self._event(db, row['id'], state, {'reason': '执行开关或执行器会话变化'})
            db.execute('INSERT OR REPLACE INTO browser_workers VALUES(?,?,?,?,?,?)',
                       (worker_id, epoch, int(payload['armed']), policy['revision'], now.isoformat(), _dump(surfaces)))
        return {'ok': True, 'protocol': PROTOCOL}

    def _surface(self, db, worker_id, epoch, revision, account_id, plan_id):
        worker = db.execute('SELECT * FROM browser_workers WHERE id=?', (worker_id,)).fetchone()
        now = self._now()
        if (not worker or not worker['armed'] or worker['epoch'] != epoch or worker['revision'] != revision
                or not 0 <= (now - _time(worker['seen_at'])).total_seconds() <= FRESH_SECONDS):
            raise ValueError('浏览器执行器未开启、会话变化或通信已过期')
        surface = next((s for s in json.loads(worker['surfaces']) if s['account_id'] == account_id and s['plan_id'] == plan_id), None)
        if not surface or not 0 <= (now - _time(surface['captured_at'])).total_seconds() <= FRESH_SECONDS:
            raise ValueError('缺少当前计划的可执行页面观察')
        return surface

    def create(self, payload, policy, *, recommendation=None):
        _shape(payload, 'worker_id epoch account_id plan_id action after reason')
        if payload['action'] not in ACTIONS:
            raise ValueError('不支持该计划动作')
        if not isinstance(payload['reason'], str) or not 2 <= len(payload['reason'].strip()) <= 500:
            raise ValueError('请填写本次具体调控原因')
        with self._tx() as db:
            self._sweep(db)
            surface = self._surface(db, payload['worker_id'], payload['epoch'], policy['revision'], payload['account_id'], payload['plan_id'])
            # Revalidate account membership against current policy, not saved worker data.
            surface = normalize_surface(surface, policy['accounts'])
            action = payload['action']; field = ACTIONS[action][0]
            if action not in surface['actions']:
                raise ValueError('当前页面未识别此动作入口；编辑表单还需在提交前核对')
            before = surface[field]
            if action == 'pause_plan':
                if before is not True or payload['after'] is not False:
                    raise ValueError('暂停只能从明确在投变为暂停')
                after = False
            else:
                after = _number(payload['after'])
                fraction = (Decimal(after) - Decimal(before)) / Decimal(before)
                if action == 'budget_up' and not 0 < fraction <= Decimal('.20'):
                    raise ValueError('预算单次上调须在20%以内')
                if action == 'budget_down' and not Decimal('-.30') <= fraction <= Decimal('-.10'):
                    raise ValueError('预算单次下调须为10%至30%')
                if action.startswith('roi_target_') and not Decimal('.05') <= abs(fraction) <= Decimal('.10'):
                    raise ValueError('ROI单次调整须为5%至10%，不自动舍入')
                if action == 'roi_target_up' and fraction <= 0 or action == 'roi_target_down' and fraction >= 0:
                    raise ValueError('ROI变更方向不匹配')
            # All actions on one plan share the cooldown, regardless of action kind.
            for old in db.execute('SELECT a.state,a.claimed_at,a.created_at,a.intent,r.resolved_at FROM browser_actions a LEFT JOIN browser_action_resolutions r ON r.action_id=a.id').fetchall():
                intent = json.loads(old['intent'])
                if intent['account_id'] != surface['account_id'] or intent['plan_id'] != surface['plan_id']:
                    continue
                if old['state'] in {'draft', 'approved', 'claimed', 'submitting', 'unknown'}:
                    raise ValueError('此计划已有待处理或结果不明的调控单')
                if old['claimed_at'] and old['state'] == 'verified' and (self._now() - _time(old['claimed_at'])).total_seconds() < 3600:
                    raise ValueError('此计划距离上一次已核验调整不足1小时')
                if old['state'] in RECONCILED and (not old['resolved_at'] or (self._now() - _time(old['resolved_at'])).total_seconds() < 3600):
                    raise ValueError('此计划距离结果核对不足1小时，不重复调整')
            now = self._now(); action_id = secrets.token_hex(16)
            intent = {'account_id': surface['account_id'], 'plan_id': surface['plan_id'], 'plan_name': surface['name'],
                      'action': action, 'action_label': ACTIONS[action][1], 'field': field, 'before': before, 'after': after,
                      'setting_roi_basis': surface['roi_basis'], 'reason': payload['reason'].strip(),
                      'surface': surface_binding(surface), 'origin': 'manual_review',
                      'note': '人工指定动作；不是规则达标认证，页面设置口径不替代账户考核口径。'}
            if recommendation is not None:
                if (recommendation.get('eligible') is not True or recommendation.get('mode') != 'simulation_only'
                        or recommendation.get('scope') != 'plan' or recommendation.get('account_id') != intent['account_id']
                        or recommendation.get('entity_id') != intent['plan_id'] or recommendation.get('action') != action):
                    raise ValueError('规则建议与本次明确计划动作不匹配')
                def expected_value(value):
                    return value if type(value) is bool else _number(str(value))
                if expected_value(recommendation.get('before')) != before or expected_value(recommendation.get('after')) != after:
                    raise ValueError('规则建议前后值与当前页面或审批不匹配')
                if action.startswith('roi_target_') and recommendation.get('metric_basis') != surface['roi_basis']:
                    raise ValueError('规则建议与计划ROI设置口径不一致')
                intent.update(origin='rule_recommendation', recommendation=recommendation_binding(recommendation),
                              note='来自当前规则建议；审批、占用、提交前均重新判断资格。')
            if self.action_validator is not None:
                self.action_validator(intent)
            digest = _hash(intent)
            db.execute('INSERT INTO browser_actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                       (action_id, 'draft', now.isoformat(), (now + timedelta(seconds=APPROVAL_SECONDS)).isoformat(),
                        payload['worker_id'], payload['epoch'], policy['revision'], _dump(intent), digest, None, None, None))
            self._event(db, action_id, 'draft', {'intent_digest': digest})
            return self._public(db.execute('SELECT * FROM browser_actions WHERE id=?', (action_id,)).fetchone())

    def _check(self, db, row, policy):
        if row['revision'] != policy['revision']:
            raise ValueError('账户或分组配置变化，请重新准备')
        intent = json.loads(row['intent'])
        surface = self._surface(db, row['worker_id'], row['epoch'], row['revision'], intent['account_id'], intent['plan_id'])
        normalize_surface(surface, policy['accounts'])
        if surface_binding(surface) != intent['surface']:
            raise ValueError('页面、能力或当前设置已变化，请取消后重新准备')
        if intent['origin'] == 'rule_recommendation':
            if self.recommendation_validator is None:
                raise ValueError('规则复核器尚未连接，不能执行规则调控单')
            self.recommendation_validator(intent['recommendation'])
        if self.action_validator is not None:
            self.action_validator(intent)
        return intent

    def has_pending(self):
        with self._tx() as db:
            self._sweep(db)
            return db.execute("SELECT 1 FROM browser_actions WHERE state IN ('draft','approved','claimed','submitting','unknown') LIMIT 1").fetchone() is not None

    def has_active_or_unknown(self):
        with self._tx() as db:
            self._sweep(db)
            return db.execute("SELECT 1 FROM browser_actions WHERE state IN ('claimed','submitting','unknown') LIMIT 1").fetchone() is not None

    def history(self, account_id):
        """Read without a write transaction, including while validating a command."""
        db = sqlite3.connect(Path(self.path).resolve().as_uri()+'?mode=ro', timeout=10, uri=True)
        db.row_factory = sqlite3.Row
        try:
            result = []
            for row in db.execute('SELECT id,state,created_at,claimed_at,intent FROM browser_actions'):
                intent = json.loads(row['intent'])
                if intent['account_id'] == account_id:
                    result.append({'id':row['id'],'account_id':account_id,'entity_id':intent['plan_id'],
                        'plan_id':intent['plan_id'],'action':intent['action'],'state':row['state'],
                        'at':row['claimed_at'] or row['created_at'],'before':intent['before'],'after':intent['after']})
            return result
        finally: db.close()

    def approve(self, payload, policy):
        _shape(payload, 'id intent_digest confirmed')
        if payload['confirmed'] is not True:
            raise ValueError('需要对此条具体调控单确认')
        with self._tx() as db:
            self._sweep(db)
            row = db.execute('SELECT * FROM browser_actions WHERE id=?', (payload['id'],)).fetchone()
            if not row or row['state'] != 'draft' or not secrets.compare_digest(row['digest'], str(payload['intent_digest'])):
                raise ValueError('审批单已失效、已确认或内容摘要不匹配')
            self._check(db, row, policy)
            db.execute("UPDATE browser_actions SET state='approved' WHERE id=?", (row['id'],))
            self._event(db, row['id'], 'approved', {'intent_digest': row['digest']})
            return {'ok': True, 'id': row['id'], 'state': 'approved', 'executed': False}

    def claim(self, payload, policy):
        _shape(payload, 'worker_id epoch')
        with self._tx() as db:
            self._sweep(db)
            if db.execute("SELECT 1 FROM browser_actions WHERE state IN ('claimed','submitting','unknown') LIMIT 1").fetchone():
                return {'ok': True, 'command': None, 'blocked': '仍有执行中或结果不明的单条动作'}
            for row in db.execute("SELECT * FROM browser_actions WHERE worker_id=? AND epoch=? AND state='approved' ORDER BY created_at,id", (payload['worker_id'], payload['epoch'])).fetchall():
                try:
                    intent = self._check(db, row, policy)
                except ValueError as exc:
                    db.execute("UPDATE browser_actions SET state='rejected' WHERE id=?", (row['id'],))
                    self._event(db, row['id'], 'rejected', {'reason': str(exc)})
                    continue
                lease = secrets.token_urlsafe(32)
                db.execute("UPDATE browser_actions SET state='claimed',claimed_at=?,lease=? WHERE id=?", (self._now().isoformat(), lease, row['id']))
                self._event(db, row['id'], 'claimed')
                return {'ok': True, 'command': {'id': row['id'], 'lease': lease, 'intent_digest': row['digest'],
                                               'intent': intent, 'expires_at': row['expires_at']}}
            return {'ok': True, 'command': None}

    def begin(self, payload, policy):
        _shape(payload, 'id lease observation')
        with self._tx() as db:
            self._sweep(db)
            row = db.execute('SELECT * FROM browser_actions WHERE id=?', (payload['id'],)).fetchone()
            if not row or row['state'] != 'claimed' or not secrets.compare_digest(row['lease'] or '', str(payload['lease'])):
                raise ValueError('动作未占用或提交许可已消费')
            if _time(row['expires_at']) <= self._now():
                raise ValueError('审批有效期已结束')
            intent = self._check(db, row, policy)
            observation = normalize_surface(payload['observation'], policy['accounts'])
            age = (self._now() - _time(observation['captured_at'])).total_seconds()
            if not 0 <= age <= 10 or surface_binding(observation) != intent['surface']:
                raise ValueError('提交前的独立页面核对不匹配或已过期')
            db.execute("UPDATE browser_actions SET state='submitting' WHERE id=?", (row['id'],))
            self._event(db, row['id'], 'submitting')
            return {'ok': True, 'id': row['id'], 'submit_once': True}

    def receipt(self, payload):
        _shape(payload, 'id lease submitted observation message')
        if type(payload['submitted']) is not bool or not isinstance(payload['message'], str) or len(payload['message']) > 500:
            raise ValueError('执行回执格式无效')
        with self._tx() as db:
            self._sweep(db)
            row = db.execute('SELECT * FROM browser_actions WHERE id=?', (payload['id'],)).fetchone()
            if not row or not secrets.compare_digest(row['lease'] or '', str(payload['lease'])):
                raise ValueError('回执不属于这条已占用动作')
            receipt = {k: v for k, v in payload.items() if k != 'lease'}
            if row['receipt']:
                if json.loads(row['receipt']) != receipt:
                    raise ValueError('同一动作已收到不同回执')
                return {'ok': True, 'id': row['id'], 'state': row['state']}
            if row['state'] in RECONCILED:
                # A delayed first acknowledgement must let the worker clear its
                # persisted receipt. Retain it as late evidence, never rerun the
                # old verification or overwrite the independent resolution.
                db.execute('UPDATE browser_actions SET receipt=? WHERE id=?', (_dump(receipt), row['id']))
                self._event(db, row['id'], 'late_receipt', {'message': payload['message'],
                            'note': '核对后到达的回执，仅留存，不改变独立核对结果。'})
                return {'ok': True, 'id': row['id'], 'state': row['state'], 'late': True}
            if row['state'] not in {'claimed', 'submitting', 'unknown'}:
                raise ValueError('动作状态不接受执行回执')
            if payload['submitted'] and row['state'] == 'claimed':
                raise ValueError('未取得一次提交许可')
            intent = json.loads(row['intent']); observation = payload['observation']
            state = 'unknown' if payload['submitted'] or row['state'] in {'submitting', 'unknown'} else 'rejected'
            if payload['submitted'] and isinstance(observation, dict):
                _shape(observation, 'account_id plan_id tab_id document_id readback_document_id field value captured_at reloaded')
                match = all(observation[k] == intent['surface'][k] for k in ('account_id', 'plan_id', 'tab_id', 'document_id'))
                age = (self._now() - _time(observation['captured_at'])).total_seconds()
                readback_document = _id(observation['readback_document_id'], opaque=True)
                submitted = db.execute("SELECT at FROM browser_action_journal WHERE action_id=? AND event='submitting' ORDER BY seq DESC LIMIT 1", (row['id'],)).fetchone()
                after_submit = submitted is not None and _time(observation['captured_at']) >= _time(submitted['at'])
                if (match and observation['field'] == intent['field'] and observation['value'] == intent['after']
                        and type(observation['value']) is type(intent['after']) and after_submit
                        and readback_document != observation['document_id']
                        and 0 <= age <= 10 and observation['reloaded'] is True):
                    state = 'verified'
            db.execute('UPDATE browser_actions SET state=?,receipt=? WHERE id=?', (state, _dump(receipt), row['id']))
            self._event(db, row['id'], state, {'submitted': payload['submitted'], 'message': payload['message'],
                                              'verification': '刷新后页面设置匹配；不证明调控效果或因果' if state == 'verified' else '未核验最终设置'})
            return {'ok': True, 'id': row['id'], 'state': state}

    def cancel(self, payload):
        _shape(payload, 'id')
        with self._tx() as db:
            self._sweep(db)
            row = db.execute('SELECT * FROM browser_actions WHERE id=?', (payload['id'],)).fetchone()
            if not row or row['state'] not in {'draft', 'approved'}:
                raise ValueError('动作已执行占用或结果不明，不能按取消处理')
            db.execute("UPDATE browser_actions SET state='cancelled' WHERE id=?", (row['id'],))
            self._event(db, row['id'], 'cancelled')
            return {'ok': True, 'id': row['id'], 'state': 'cancelled'}

    def _reconciliation_evidence(self, db, row, policy, worker_id, epoch):
        if not row or row['state'] != 'unknown' or not row['claimed_at']:
            raise ValueError('仅可核对已占用且结果不明的动作')
        if worker_id != row['worker_id']:
            raise ValueError('必须由原执行器重新观察，不能替换其他执行器')
        intent = json.loads(row['intent'])
        current = normalize_surface(self._surface(db, worker_id, epoch, policy['revision'],
                                    intent['account_id'], intent['plan_id']), policy['accounts'])
        original = intent['surface']
        if any(current[k] != original[k] for k in ('tab_id', 'path', 'group')):
            raise ValueError('请回到原标签的同一计划页面进行核对')
        if current['document_id'] == original['document_id'] or _time(current['captured_at']) <= _time(row['claimed_at']):
            raise ValueError('请先刷新原计划页面，等待新文档的当前设置上报')
        field = intent['field']; value = current[field]
        if value is None or type(value) is not type(intent['after']):
            raise ValueError('刷新后仍缺少明确的当前设置，保持结果不明')
        if field == 'target_roi' and current['roi_basis'] != intent['setting_roi_basis']:
            raise ValueError('当前ROI设置口径已改变，不能据此解除结果不明')
        match = 'target' if value == intent['after'] else 'before' if value == intent['before'] else 'other'
        return {'revision': policy['revision'], 'worker_id': worker_id, 'epoch': epoch,
                'intent_digest': row['digest'], 'surface': current, 'match': match, 'value': value}

    @staticmethod
    def _reconciliation_binding(evidence):
        # A later inventory of identical settings may refresh capture time without
        # changing the reviewed values, document, worker session or policy.
        return {**evidence, 'surface': surface_binding(evidence['surface'])}

    def reconcile_preview(self, payload, policy):
        _shape(payload, 'id worker_id epoch')
        with self._tx() as db:
            self._sweep(db)
            row = db.execute('SELECT * FROM browser_actions WHERE id=?', (payload['id'],)).fetchone()
            evidence = self._reconciliation_evidence(db, row, policy, payload['worker_id'], payload['epoch'])
            now = self._now(); expires = now + timedelta(seconds=RECONCILE_SECONDS)
            digest = _hash({'nonce': secrets.token_hex(16), 'evidence': self._reconciliation_binding(evidence)})
            db.execute('INSERT OR REPLACE INTO browser_action_resolutions VALUES(?,?,?,?,?,NULL)',
                       (row['id'], digest, now.isoformat(), expires.isoformat(), _dump(evidence)))
            self._event(db, row['id'], 'reconciliation_preview', {'digest': digest, 'match': evidence['match']})
            return {'ok': True, 'id': row['id'], 'reconciliation_digest': digest, 'expires_at': expires.isoformat(),
                    'intent': json.loads(row['intent']), 'evidence': evidence, 'executed': False}

    def reconcile_confirm(self, payload, policy):
        _shape(payload, 'id reconciliation_digest confirmed')
        if payload['confirmed'] is not True:
            raise ValueError('需要明确确认刷新后的当前设置及解除队列暂停')
        with self._tx() as db:
            self._sweep(db)
            row = db.execute('SELECT * FROM browser_actions WHERE id=?', (payload['id'],)).fetchone()
            preview = db.execute('SELECT * FROM browser_action_resolutions WHERE action_id=?', (payload['id'],)).fetchone()
            if (not preview or preview['resolved_at'] or _time(preview['expires_at']) <= self._now()
                    or not secrets.compare_digest(preview['digest'], str(payload['reconciliation_digest']))):
                raise ValueError('核对单已过期、已确认或摘要不匹配，请重新读取')
            previous = json.loads(preview['evidence'])
            evidence = self._reconciliation_evidence(db, row, policy, previous['worker_id'], previous['epoch'])
            if self._reconciliation_binding(previous) != self._reconciliation_binding(evidence):
                raise ValueError('核对后页面设置或执行会话已变化，请重新读取')
            state = 'reconciled_' + evidence['match']; now = self._now().isoformat()
            db.execute('UPDATE browser_action_resolutions SET evidence=?,resolved_at=? WHERE action_id=?',
                       (_dump(evidence), now, row['id']))
            db.execute('UPDATE browser_actions SET state=? WHERE id=?', (state, row['id']))
            self._event(db, row['id'], state, {'digest': preview['digest'], 'value': evidence['value'],
                        'note': '独立核对当前设置；原提交及变化归因仍未知。不重试原动作，按核对时间保留对应对象的冷却。'})
            return {'ok': True, 'id': row['id'], 'state': state, 'executed': False}

    @staticmethod
    def _public(row, resolution=None):
        return {'id': row['id'], 'state': row['state'], 'created_at': row['created_at'], 'expires_at': row['expires_at'],
                'worker_id': row['worker_id'], 'epoch': row['epoch'], 'intent_digest': row['digest'],
                'intent': json.loads(row['intent']), 'receipt': json.loads(row['receipt']) if row['receipt'] else None,
                'resolution': {'resolved_at': resolution['resolved_at'], 'evidence': json.loads(resolution['evidence'])}
                              if resolution and resolution['resolved_at'] else None}

    def status(self, policy):
        with self._tx() as db:
            self._sweep(db); now = self._now(); workers = []
            for row in db.execute('SELECT * FROM browser_workers ORDER BY seen_at DESC').fetchall():
                current = row['revision'] == policy['revision'] and 0 <= (now - _time(row['seen_at'])).total_seconds() <= FRESH_SECONDS
                surfaces = []
                if current:
                    for s in json.loads(row['surfaces']):
                        try:
                            normalize_surface(s, policy['accounts'])
                            if 0 <= (now - _time(s['captured_at'])).total_seconds() <= FRESH_SECONDS:
                                surfaces.append(s)
                        except ValueError:
                            continue
                workers.append({'worker_id': row['id'], 'epoch': row['epoch'], 'armed': bool(row['armed']) and current,
                                'current': current, 'seen_at': row['seen_at'], 'surfaces': surfaces})
            actions = [self._public(row, db.execute('SELECT * FROM browser_action_resolutions WHERE action_id=?', (row['id'],)).fetchone())
                       for row in db.execute('SELECT * FROM browser_actions ORDER BY created_at DESC,id DESC LIMIT 100').fetchall()]
            return {'ok': True, 'protocol': PROTOCOL, 'mode': 'per_action_confirmation', 'workers': workers, 'actions': actions,
                    'execution_available': any(w['armed'] and any(s['actions'] for s in w['surfaces']) for w in workers),
                    'note': '单条人工确认；页面执行适配器未连接时不可执行。点击回执不算核验成功。'}
