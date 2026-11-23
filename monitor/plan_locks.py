"""Shared SQLite reservation checks; no account IO or automatic unlock."""
import json
from datetime import datetime


def lock_identity(context):
    if isinstance(context,dict) and context.get('scope')=='control_create':
        if (context.get('action') not in {'one_click_boost','create_chase'} or context.get('entity_id') is not None
            or not isinstance(context.get('account_id'),str) or not context['account_id']
            or not isinstance(context.get('parent_plan_id'),str) or not context['parent_plan_id']):
            raise ValueError('创建记录须明确账户和母计划，不能伪造尚未生成的任务ID')
        if context.get('action')=='create_chase' and (not isinstance(context.get('material_id'),str) or not context['material_id']):
            raise ValueError('素材追投创建还须明确原生素材ID')
        return lock_identity({'scope':'plan','account_id':context['account_id'],'entity_id':context['parent_plan_id']})
    if (not isinstance(context,dict) or not isinstance(context.get('account_id'),str) or not context['account_id']
            or not isinstance(context.get('entity_id'),str) or not context['entity_id']):
        raise ValueError('提交缺少明确对象证据')
    scope=context.get('scope','plan')
    if 'scope' not in context and context.get('action') in {'pause_control','control_budget_up'}:
        raise ValueError('历史任务提交缺少作用域，不能按计划ID处理')
    def part(value):
        if value.isascii() and value.isdecimal():
            number=int(value)
            if not 0<number<=2**64-1:raise ValueError('提交对象ID超出范围')
            return str(number)
        return value
    if scope=='plan':return part(context['account_id']),part(context['entity_id'])
    if scope=='control' and isinstance(context.get('parent_plan_id'),str) and context['parent_plan_id']:
        return part(context['account_id']),part(context['parent_plan_id'])
    raise ValueError('任务提交缺少明确母计划，或对象作用域未知')


def has_table(db,name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None


def check_deadline_cooldowns(db,identity,now):
    if not has_table(db,'control_deadlines'):return
    for raw, in db.execute("SELECT data_json FROM control_deadlines WHERE account_id=? AND parent_plan_id=? AND state='closed_observed'",identity):
        value=json.loads(raw)
        if value.get('end_attempted') is not True:continue
        at=value.get('end_claimed_at')
        if at is None and has_table(db,'control_deadline_journal'):
            row=db.execute("SELECT at FROM control_deadline_journal WHERE deadline_id=? AND event='end_claimed' ORDER BY seq DESC LIMIT 1",(value['id'],)).fetchone()
            at=row[0] if row else None
        try:
            claimed=datetime.fromisoformat(at)
            receipt=value.get('end_receipt') or {}
            if receipt.get('native_origin_verified') is True and receipt.get('sent_at'):
                anchor=max(claimed,datetime.fromisoformat(receipt['sent_at']))
            else:
                # Unknown send time: use later closure, not a guessed IO clock.
                anchor=max(claimed,datetime.fromisoformat(value.get('closed_at')))
            age=(now-anchor).total_seconds()
        except (TypeError,ValueError):raise ValueError('历史截止结束时点不足，不能绕过母计划冷却') from None
        if age<3600:raise ValueError('母计划距最近截止结束请求尚未满一小时，不能叠加调整')


def check_reservations(db,identity,context=None,now=None):
    if now is not None:check_deadline_cooldowns(db,identity,now)
    if has_table(db,'smart_control_runs') and db.execute(
            'SELECT 1 FROM smart_control_runs WHERE account_id=? AND parent_plan_id=? AND released_at IS NULL',identity).fetchone():
        raise ValueError('同账户母计划有尚未独立核对停止的控量占用')
    owned=False
    if has_table(db,'control_deadlines'):
        for job_id,raw in db.execute('SELECT id,data_json FROM control_deadlines WHERE account_id=? AND parent_plan_id=? AND released_at IS NULL',identity):
            job=json.loads(raw)
            owned=(isinstance(context,dict) and context.get('deadline_id')==job_id
                and context.get('scope')=='control_create'
                and (context.get('action'),job.get('control_type')) in {('one_click_boost','one_click_boost'),('create_chase','material_chase')}
                and (job.get('control_type')!='material_chase' or context.get('material_id')==job.get('material_id'))
                and job.get('state')=='reserved_before_creation'
                and context.get('requested_duration_hours')==job.get('duration_hours')
                and context.get('request_digest')==job.get('creation_request_digest'))
            if not owned:raise ValueError('同账户母计划有尚未核对结束的任务截止占用')
            if now is None or not 0<=(now-datetime.fromisoformat(job['reserved_at'])).total_seconds()<=60:
                raise ValueError('创建截止预留已超过发送前有效窗口')
    if isinstance(context,dict) and context.get('deadline_id') is not None and not owned:
        raise ValueError('创建请求的截止预留已改变或不存在')
    if has_table(db,'action_reservations') and db.execute(
            'SELECT 1 FROM action_reservations WHERE account_id=? AND plan_id=? AND released_at IS NULL',identity).fetchone():
        raise ValueError('同账户母计划有尚未结束的素材流程预留，禁止叠加调整')


def check_attempts(db,identity,now,cooldown_seconds):
    check_deadline_cooldowns(db,identity,now)
    if not has_table(db,'submission_attempts'):return
    for created,state,context in db.execute(
            "SELECT created_at,state,context_json FROM submission_attempts WHERE state IN ('claimed','unknown','confirmed')"):
        try:
            prior=json.loads(context or '{}');prior_identity=lock_identity(prior)
        except (TypeError,ValueError):
            raise ValueError('历史提交缺少对象证据，需先独立对账') from None
        if prior_identity!=identity:continue
        if state in {'claimed','unknown'}:raise ValueError('同账户母计划有结果未知的提交，禁止新建素材流程')
        try:
            cooldown=max(3600,cooldown_seconds,prior.get('cooldown_seconds',3600))
            if (now-datetime.fromisoformat(created)).total_seconds()<cooldown:
                raise ValueError('同账户母计划尚未满足提交后的冷却间隔')
        except (TypeError,OverflowError):
            raise ValueError('历史提交时间或冷却证据无效') from None
