"""Visible native log rows are observations, never execution acknowledgements."""
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

CN = timezone(timedelta(hours=8))
FIELDS = {'occurred_at', 'kind', 'control_id', 'before_budget', 'after_budget'}


def _clock(value):
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
        raise ValueError('日志时间须包含日期和明确时区')
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(CN)


def plan_operations_from_snapshot(snapshot):
    context = snapshot.get('context', {})
    if not isinstance(context, dict) or 'observed_plan_operations' not in context:
        return []
    source = snapshot.get('source', {})
    address = urlsplit(source.get('url', ''))
    if (snapshot.get('scope') != 'plan' or source.get('kind') != 'browser_dom'
            or source.get('segment') != 'plan_operation_log'
            or address.scheme != 'https' or address.hostname != 'qianchuan.jinritemai.com'
            or address.path != '/uni-prom/detail'
            or parse_qs(address.query).get('aavid') != [snapshot.get('account_id')]
            or context.get('plan_id') != snapshot.get('entity_id')):
        raise ValueError('可见操作日志须属于明确账户和直播详情母计划')
    items = context['observed_plan_operations']
    if not isinstance(items, list) or not 1 <= len(items) <= 200:
        raise ValueError('可见操作日志须包含1到200行')
    captured = _clock(source.get('captured_at'))
    entries = {}
    for item in items:
        if (not isinstance(item, dict) or set(item) != FIELDS
                or item.get('kind') != 'control_budget_change'
                or not isinstance(item.get('control_id'), str)
                or not re.fullmatch(r'\d{6,30}', item['control_id'])):
            raise ValueError('可见操作日志仅接受明确任务预算修改字段，不接收原始内容或操作人')
        at = _clock(item['occurred_at'])
        if at > captured:
            raise ValueError('日志操作时间不能晚于采集时间')
        for field in ('before_budget', 'after_budget'):
            value = item[field]
            if (type(value) not in (int, float) or not math.isfinite(value)
                    or not 0 <= value <= 1e15):
                raise ValueError('日志预算须为有限非负数字')
        normalized = {**item, 'occurred_at': at.isoformat()}
        key = (normalized['occurred_at'], normalized['kind'], normalized['control_id'])
        if key in entries and entries[key] != normalized:
            raise ValueError('同一可见日志出现冲突的前后值')
        entries[key] = normalized
    return sorted(entries.values(), key=lambda row: (row['occurred_at'], row['control_id']), reverse=True)
