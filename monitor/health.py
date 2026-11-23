"""Local collection evidence. Communication, collection and data clocks stay separate."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from .engine import CN, _record, _settings, _time


def _now(value):
    value = value if isinstance(value, datetime) else datetime.now(CN)
    return value.replace(tzinfo=CN) if value.tzinfo is None else value.astimezone(CN)


def _clock(value, now, limit):
    parsed = _time(value)
    if parsed is None:
        return 'missing', None
    age = (now-parsed).total_seconds()
    if age < -120:
        return 'invalid_time', round(age, 1)
    return ('recent' if age <= limit else 'expired'), round(max(0, age), 1)


def source_health(snapshots, settings, now=None):
    """Review the newest capture, even when an older capture has a later data_at."""
    now = _now(now)
    rules, accounts = _settings(settings)
    groups = defaultdict(list)
    for raw in snapshots if isinstance(snapshots, list) else []:
        try:
            record = _record(raw, accounts, now)
        except (TypeError, ValueError, AttributeError, OverflowError):
            record = None
        if record:
            groups[record['key']].append(record)
    sources = []
    for key, rows in sorted(groups.items(), key=lambda item: tuple(map(str, item[0]))):
        rows.sort(key=lambda r: r['captured'])
        last = rows[-1]
        peers = [r for r in rows if r['captured'] == last['captured']]
        clocks = {r['data'] for r in peers}
        src, raw = last['source'], last['raw']
        capture_state, capture_age = _clock(src.get('captured_at'), now, rules['max_age_seconds'])
        data_state, data_age = _clock(src.get('data_at'), now, rules['max_age_seconds'])
        previous = [r['data'] for r in rows if r['captured'] < last['captured'] and r['data'] is not None]
        progress = 'unknown'
        if len(clocks) > 1:
            data_state, progress = 'conflicting_time', 'conflicting_time'
        elif last['data'] is not None and previous:
            greatest = max(previous)
            progress = 'regressed' if last['data'] < greatest else 'advancing' if last['data'] > greatest else 'unchanged'
        if last['data'] and last['data'] > last['captured'] + timedelta(seconds=120):
            data_state = 'invalid_time'
        if src.get('metric_period', 'day') == 'day' and last['date'] != now.date():
            data_state = 'historical'
        elif last['data'] and src.get('metric_period', 'day') == 'day' and last['data'].date() != last['date']:
            data_state = 'date_mismatch'
        current = capture_state == 'recent' and data_state == 'recent' and progress not in {'regressed', 'conflicting_time'}
        sources.append({'account_id': key[0], 'entity_id': key[1], 'scope': key[2],
            'source_key': ':'.join(map(str, key[3:])), 'source_kind': src['kind'],
            'metric_basis': last['basis'], 'metric_date': src.get('metric_date'),
            'metric_period': src.get('metric_period', 'day'), 'snapshot_id': raw.get('id'),
            'captured_at': src.get('captured_at'), 'data_at': src.get('data_at'),
            'capture_state': capture_state, 'capture_age_seconds': capture_age,
            'data_state': data_state, 'data_age_seconds': data_age, 'progress': progress,
            'distinct_capture_count': len({r['captured'] for r in rows}),
            'clock_current': current, 'synthetic': src['kind'] == 'demo',
            'note': '仅检查已观察来源的时间，不认证全量覆盖、指标口径或策略资格；时间不前进不等于无消耗。'})
    summaries = []
    for aid in accounts:
        own = [s for s in sources if s['account_id'] == aid]
        real = [s for s in own if not s['synthetic']]
        recent = [s for s in real if s['clock_current']]
        if recent:
            state = 'partial_observation'
        elif not real:
            state = 'synthetic_only' if own else 'missing'
        elif any(s['progress'] in {'regressed', 'conflicting_time'} or s['data_state'] in {'invalid_time', 'conflicting_time', 'date_mismatch'} for s in real):
            state = 'data_conflict'
        elif any(s['data_state'] in {'expired', 'historical'} or s['capture_state'] == 'expired' for s in real):
            state = 'expired'
        else:
            state = 'data_time_missing'
        summaries.append({'account_id': aid, 'state': state, 'observed_sources': len(real),
            'clock_current_sources': len(recent), 'synthetic_sources': len(own)-len(real),
            'unverified_coverage': True,
            'note': '无当前数据时不能推断账户零消耗、断流或健康；新通信不能更新旧指标时间。'})
    return {'checked_at': now.isoformat(), 'max_age_seconds': rules['max_age_seconds'],
            'accounts': summaries, 'sources': sources, 'continuous_coverage_verified': False}


def runtime_health(diagnostics, cycles, run_id, started_at, poll_seconds, now=None):
    now = _now(now)
    limit = max(180, poll_seconds*3)
    rows = sorted(cycles, key=lambda c: c['id'])
    current = [c for c in rows if c['run_id'] == run_id]
    last = current[-1] if current else None
    clock = (last.get('finished_at') or last.get('started_at')) if last else started_at
    clock_state, age = _clock(clock, now, limit)
    state = 'stalled' if clock_state == 'expired' else 'invalid_time' if clock_state != 'recent' else (
        last['state'] if last else 'starting')
    gaps = []
    for a, b in zip(rows, rows[1:]):
        end, start = _time(a.get('finished_at')), _time(b.get('started_at'))
        if end and start and (start-end).total_seconds() > limit:
            gaps.append({'previous_finished_at': a['finished_at'], 'next_started_at': b['started_at'],
                         'seconds': round((start-end).total_seconds(), 1), 'across_restart': a['run_id'] != b['run_id']})
    ext = diagnostics.get('extension', {})
    seen = [v for v in (ext.get('last_seen'), ext.get('last_ingest_at')) if _time(v)]
    seen = max(seen, key=_time) if seen else None
    ext_state, ext_age = _clock(seen, now, 180)
    old_open = [c for c in rows if c['run_id'] != run_id and c['state'] == 'running']
    return {'checked_at': now.isoformat(), 'service_started_at': started_at,
        'collector': {'state': state, 'last_cycle_started_at': last.get('started_at') if last else None,
            'last_cycle_finished_at': last.get('finished_at') if last else None,
            'age_seconds': age, 'poll_seconds': poll_seconds, 'stale_after_seconds': limit,
            'last_error_type': last.get('error_type') if last else None},
        'browser_communication': {'state': ext_state, 'last_seen_at': seen, 'age_seconds': ext_age,
            'collector': ext.get('collector'), 'note': '收到通信只证明本地收到请求，不证明页面或指标已更新。'},
        'history': {'retained_cycles': len(rows), 'successful_cycles': sum(c['state'] == 'ok' for c in rows),
            'error_cycles': sum(c['state'] == 'error' for c in rows), 'previous_unfinished_cycles': len(old_open),
            'restart_transitions': sum(a['run_id'] != b['run_id'] for a, b in zip(rows, rows[1:])),
            'observed_gaps': gaps[-20:], 'first_cycle_started_at': rows[0]['started_at'] if rows else None,
            'note': '仅保存本服务实际记录的轮次；旧轮次未结束不证明故障原因，不补造停机期间的采样。'},
        'continuous_coverage_verified': False}
