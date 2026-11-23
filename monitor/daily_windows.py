"""Explicit daily ledger boundaries for linked ROI recovery; no counter stitching."""
import math
from datetime import datetime, timedelta

from .engine import CN, _time
from .outcomes import _valid_row, _window, OBSERVATION_NOTES, DERIVED_OBSERVATION_NOTES


def _midnight(day):
    return datetime(day.year, day.month, day.day, tzinfo=CN)


def _boundary(row, role):
    ctx = row['raw'].get('context', {})
    return (isinstance(ctx, dict) and ctx.get('day_boundary_role') == role
        and ctx.get('day_boundary_verified') is True
        and row['source'].get('kind') in {'import', 'demo'}
        and row['source'].get('coverage') == 'report'
        and row['source'].get('metric_period', 'day') == 'day'
        and row['data'] == _midnight(row['date']) + (timedelta(days=1) if role == 'closing' else timedelta()))


def valid_daily_row(row):
    if row['source'].get('metric_period', 'day') != 'day':
        return False
    if _boundary(row, 'closing'):
        allowed = OBSERVATION_NOTES | DERIVED_OBSERVATION_NOTES | {'数据时间与统计日期不一致'}
        return (row['data'] <= row['captured'] + timedelta(minutes=2)
                and not any(issue not in allowed for issue in row['issues']))
    return _valid_row(row)


def inspect_daily_ledger(rows, fields, rules):
    """Verify continuous observation and explicitly observed daily reset pairs."""
    ordered = sorted(rows, key=lambda r: (r['data'], r['date'], r['captured']))
    if not ordered or any(not valid_daily_row(r) for r in ordered):
        return ['日累计质量或午夜封账声明不完整'], []
    signatures = {}
    for row in ordered:
        values = tuple(row['metrics'].get(k) for k in sorted(fields))
        if any(value is None for value in values):
            return ['恢复期间日累计金额、订单或消耗缺失'], []
        key = row['date'], row['data']
        if key in signatures and signatures[key] != values:
            return ['同一日台账时刻存在冲突累计值'], []
        signatures[key] = values
    if any((b['data']-a['data']).total_seconds() > rules['max_gap_minutes']*60
           for a, b in zip(ordered, ordered[1:])):
        return ['恢复链的连续采集存在断档'], []
    days = sorted({r['date'] for r in ordered})
    boundaries = []
    for day in days:
        own = [r for r in ordered if r['date'] == day]
        if any(b['metrics'][k]+1e-8 < a['metrics'][k] for a, b in zip(own, own[1:]) for k in fields):
            return ['日内累计回退，不能当作午夜重置'], []
    for a, b in zip(days, days[1:]):
        if b-a != timedelta(days=1):
            return ['恢复链缺少某个自然日的台账'], []
        closing = [r for r in ordered if r['date'] == a and _boundary(r, 'closing')]
        opening = [r for r in ordered if r['date'] == b and _boundary(r, 'opening')]
        if not closing or not opening:
            return ['跨日恢复需要前日精确封账与次日精确起点，不能猜测重置'], []
        if any(r['metrics'][k] != 0 for r in opening for k in fields):
            return ['已声明的次日起点不是同口径零累计，不能认证重置边界'], []
        boundaries.append({'closing_metric_date': a.isoformat(), 'opening_metric_date': b.isoformat(),
            'at': _midnight(b).isoformat(), 'declared_verified': True, 'platform_verified': False,
            'closing_counters': {k: closing[-1]['metrics'][k] for k in sorted(fields)},
            'opening_counters': {k: opening[-1]['metrics'][k] for k in sorted(fields)}})
    return [], boundaries


def daily_window(rows, start, end, fields, changed_field, declared_value, rules):
    result = {'complete': False, 'start': start.isoformat(), 'end': end.isoformat(),
              'sample_count': 0, 'delta': {}, 'reason': '缺少完整的独立日台账窗口', 'segments': []}
    if end <= start:
        return result
    cursor = start
    segments = []
    stamps = set()
    while cursor < end:
        boundary = _midnight(cursor.date()) + timedelta(days=1)
        finish = min(boundary, end)
        own = [r for r in rows if r['date'] == cursor.date()]
        if finish == boundary and not any(_boundary(r, 'closing') for r in own):
            result['reason'] = '窗口到午夜需要明确的前日封账'; return result
        if cursor > start:
            opening = [r for r in own if _boundary(r, 'opening')]
            if not opening or any(r['metrics'].get(k) != 0 for r in opening for k in fields):
                result['reason'] = '窗口跨午夜缺少已核对的零累计起点'; return result
        part = _window(own, cursor, finish, fields, changed_field, declared_value, rules,
                       validator=valid_daily_row, minimum_points=2)
        if not part['complete']:
            result['reason'] = part['reason']; return result
        part['metric_date'] = cursor.date().isoformat()
        segments.append(part)
        stamps.update(r['data'] for r in own if _time(part['start']) <= r['data'] <= _time(part['end']))
        cursor = finish
    expected = (end-start).total_seconds()
    if len(stamps) < max(3, math.ceil(expected/(rules['max_gap_minutes']*60))+1):
        result['reason'] = '整体窗口的独立时间样本不足'; return result
    result.update(complete=True, start=segments[0]['start'], end=segments[-1]['end'], reason='',
                  sample_count=len(stamps), segments=segments,
                  delta={k: math.fsum(part['delta'][k] for part in segments) for k in fields})
    return result
