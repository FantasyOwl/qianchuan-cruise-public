"""Export a bounded, credential-free monitor snapshot; never submit mutations.

One local GET at most. SQLite fallback is explicitly labelled and read-only.
Does not import App/OfficialAPI or decrypt credentials. Output is business data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
CN = timezone(timedelta(hours=8))
PENDING = {'draft', 'checking', 'submitting', 'unknown', 'submitted_pending_verification'}


def clean(value):
    """Bound text and remove common credentials/URLs from error strings."""
    if isinstance(value, str):
        value = re.sub(r'https?://[^\s<>"\']+', '[URL omitted]', value)
        value = re.sub(r'(?i)((?:access_token|refresh_token|app_secret|csrf(?:_token)?|authorization|pairing_code|x-qianchuan-ego)\s*[=:]\s*)[^\s,;，；]+', r'\1[redacted]', value)
        return value[:500]
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()
                if not re.search(r'(?i)token|secret|csrf|cookie|password|pairing|^authorization$|nonce|(?:^|_)url$', str(k))}
    if isinstance(value, list):
        return [clean(v) for v in value[:100]]
    return value


def fields(value, keys):
    return {k: clean(value[k]) for k in keys if k in value}


def age(value, now):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if dt.tzinfo is None:
            return None
        return round((now - dt).total_seconds(), 1)
    except (ValueError, TypeError, AttributeError):
        return None


def load_json(path, default):
    try:
        return json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        return default


@contextmanager
def connect(path):
    # URI mode=ro must never silently create a missing production database.
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def action_row(row, now):
    intent = json.loads(row['intent']) if isinstance(row.get('intent'), str) else row.get('intent', {})
    result = json.loads(row['result'] or '{}') if isinstance(row.get('result'), str) else row.get('result') or {}
    reply = result.get('reply') or {}
    return {
        **fields(row, ('id', 'state', 'created_at', 'updated_at', 'expires_at')),
        **fields(intent, ('account_id', 'plan_id', 'action', 'task_id', 'before', 'after')),
        'channel': intent.get('execution_channel', 'api'),
        'feature_id': (intent.get('rule_check') or {}).get('feature_id'),
        'material_count': len(intent.get('material_ids') or []),
        'age_seconds': age(row.get('updated_at'), now),
        'result': {**fields(result, ('message', 'phase', 'native_code', 'operation', 'reason', 'origin',
                                    'request_id', 'post_sent', 'submission_uncertain', 'ad_changes', 'task_id')),
                   'acknowledged_task_id': str(reply['id']) if reply.get('id') else None},
        **fields(row, ('readback_available', 'hold_available')),
    }


def run_row(row, kind, now):
    d = json.loads(row['data'])
    command = d.get('command') or {}
    error = d.get('last_error') or {}
    return {
        **fields(row, ('id', 'account_id', 'plan_id', 'state', 'updated_at')),
        'kind': kind,
        **fields(d, ('phase', 'action_id', 'message', 'preview', 'single_test', 'isolated_readback',
                     'readback_only', 'yielding', 'paused', 'auto_retry_paused', 'retry_at',
                     'last_progress_at', 'started_at', 'completed', 'pages_read', 'tasks_read',
                     'materials_read', 'material_source', 'material_captured_at', 'inventory_source', 'inventory_at', 'direct_inventory_complete', 'day')),
        'last_error': fields(error, ('code', 'title', 'hint', 'attempt', 'at', 'phase')),
        'command': fields(command, ('id', 'expires_at', 'phase')),
        'age_seconds': age(row.get('updated_at'), now),
        'progress_age_seconds': age(d.get('last_progress_at') or row.get('updated_at'), now),
    }


def profile_row(profile, bindings):
    value = [profile, bindings]
    revision = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    result = fields(profile, ('enabled', 'main_interval_seconds', 'auto_interval_seconds',
                              'operation_start', 'operation_end', 'snapshot_max_age_seconds',
                              'broadcast_protection'))
    result['features_enabled'] = {k: v.get('enabled') for k, v in profile.get('features', {}).items()}
    result['revision'] = revision
    return result


def collect(*, offline=False, detail=False, timeout=8):
    now = datetime.now(CN)
    s = None
    service = {'attempted': not offline, 'reachable': None, 'source': 'sqlite_only'}
    if not offline:
        started = time.monotonic()
        try:
            # Fixed loopback destination; never accept a user-supplied remote URL.
            with build_opener(ProxyHandler({})).open('http://127.0.0.1:8877/api/cruise', timeout=timeout) as r:
                s = json.load(r)
            if not isinstance(s, dict) or not isinstance(s.get('accounts'), list):
                raise ValueError('Unexpected local state schema')
            service.update(reachable=True, source='local_http_and_sqlite', server_at=s.get('now'))
        except (OSError, ValueError) as exc:
            s = None
            service.update(reachable=False, source='sqlite_fallback', error_type=type(exc).__name__)
        service['elapsed_seconds'] = round(time.monotonic() - started, 2)
    out = {'schema_version': 1, 'collected_at': now.isoformat(), 'timezone': 'Asia/Shanghai',
           'project_root': str(ROOT), 'read_only': True, 'service': service,
           'source_consistency': 'separate_local_reads_not_atomic', 'errors': [], 'accounts': [],
           'pending_actions': [], 'open_runs': [], 'latest_runs': [], 'rate_control': {}}
    cfg = load_json(ROOT / 'config.json', {})
    out['execution_strategy']=fields((s or {}).get('execution_strategy') or load_json(ROOT/'data/execution-strategy.json',{}),('mode','version'))
    out['budget_recovery']={'policy':fields(((s or {}).get('api_budget_recovery') or {}).get('policy') or load_json(ROOT/'data/budget-recovery.json',{}),
        ('version','enabled','scope','roi_basis','increase_ratio','cooldown_seconds')),'accounts':[]}
    accounts = {a['id']: fields(a, ('id', 'name', 'scene', 'observe', 'delivery_entry', 'roi_basis'))
                for a in cfg.get('accounts', [])}
    if s:
        for a in s['accounts']:
            base = accounts.setdefault(a['id'], fields(a, ('id', 'name', 'scene', 'observe', 'delivery_entry', 'roi_basis')))
            base['profile'] = profile_row(a.get('profile', {}), a.get('bindings', {}))
            base['lanes'] = {k: fields(v, ('running', 'next_at', 'last_at')) for k, v in a.get('lanes', {}).items()}
        e = s.get('ego_executor') or {}
        out['executor'] = fields(e, ('provider', 'enabled', 'armed', 'running', 'current', 'seen_at', 'busy', 'suspended', 'version', 'message', 'error','mode','max_parallel','control_return_error'))
        out['executor']['account_workers']=[fields(w,('account_id','name','paused','suspended','running','current','seen_at','busy','message','error','space_id','phase','last_stage_at','last_completed_at','automatic_control_return')) for w in e.get('account_workers',[])]
        out['executor']['heartbeat_age_seconds'] = age(e.get('seen_at'), now)
        out['executor']['scheduler'] = fields(e.get('scheduler') or {}, ('policy', 'quantum', 'last_account_id', 'queue'))
        out['api_workflows'] = [{**fields(w, ('account_id', 'plan_id', 'captured_at', 'date_start', 'date_end',
                 'tasks_complete', 'pages_read', 'scanned_rows', 'material_total', 'materials_complete',
                 'eligible_range_complete', 'stop_reason', 'materials_captured_at')),
                 'key': key, 'ready_batches': sum(bool(b.get('ready')) for b in w.get('batches', []))}
                 for key, w in s.get('api_workflows', {}).items()]
    else:
        out['executor'] = fields(load_json(ROOT / 'data/ego-executor.json', {}), ('provider', 'enabled', 'armed'))
        out['executor'].update(current=None, heartbeat_age_seconds=None, source='persisted_settings_not_live_health')
        out['api_workflows'] = None
    try:
        with connect(ROOT / 'data/cruise-runtime.db') as db:
            for row in db.execute('SELECT * FROM cruise_accounts'):
                aid = row['account_id']
                base = accounts.setdefault(aid, {'id': aid})
                profile, bindings = json.loads(row['profile']), json.loads(row['bindings'])
                base['profile'] = profile_row(profile, bindings)
                base['lanes'] = {}
                for lane in ('main', 'auto'):
                    last = db.execute('SELECT at,result FROM cruise_rounds WHERE account_id=? AND lane=? ORDER BY at DESC,id DESC LIMIT 1', (aid, lane)).fetchone()
                    value = {'running': bool(row[lane + '_running']), 'next_at': row[lane + '_due'], 'last_at': last['at'] if last else None}
                    if last:
                        value['result'] = fields(json.loads(last['result']), ('summary', 'ad_changes', 'rule_candidates', 'proposals', 'missing_fields', 'missing_page_binding', 'material_batches', 'material_rows_scanned'))
                    base['lanes'][lane] = value
                if detail:
                    base['rule_details'] = fields(profile, ('features', 'safety'))
                base['recent_logs'] = [{**fields(dict(r), ('id', 'at', 'event')),
                                       'detail': fields(json.loads(r['detail']), ('message', 'summary', 'state', 'operation', 'reason', 'ad_changes', 'action_id', 'task_id'))}
                                      for r in db.execute('SELECT id,at,event,detail FROM cruise_logs WHERE account_id=? ORDER BY id DESC LIMIT ?', (aid, 8 if detail else 3))]
    except (OSError, ValueError, sqlite3.Error) as exc:
        out['errors'].append({'source': 'cruise-runtime.db', 'error_type': type(exc).__name__})
    try:
        with connect(ROOT / 'data/official-api-actions.db') as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='api_budget_recovery'").fetchone():
                out['budget_recovery']['accounts']=[{**fields(dict(r),('account_id','next_at','state','message')),'details':clean(json.loads(r['details']))} for r in db.execute('SELECT * FROM api_budget_recovery')]
            for table, key in (('api_auto', 'f5_authorization'), ('api_rule_auto', 'rules_authorization')):
                for row in db.execute('SELECT * FROM ' + table):
                    v = dict(row)
                    if isinstance(v.get('features'), str):
                        v['features'] = json.loads(v['features'])
                    accounts.setdefault(v['account_id'], {'id': v['account_id']})[key] = fields(v, (
                        'plan_id', 'profile_revision', 'enabled', 'execution_mode', 'features', 'next_at', 'state', 'message',
                        'budget_cap', 'batch_cap', 'budget_unlimited', 'batches_unlimited'))
            pending = db.execute("SELECT * FROM api_actions WHERE state IN ('draft','checking','submitting','unknown','submitted_pending_verification') ORDER BY updated_at DESC LIMIT 101").fetchall()
            out['pending_actions_truncated'] = len(pending) > 100
            out['pending_actions'] = [action_row(dict(r), now) for r in pending[:100]]
            for aid, a in accounts.items():
                rows = db.execute("SELECT * FROM api_actions WHERE json_extract(intent,'$.account_id')=? ORDER BY updated_at DESC LIMIT ?", (aid, 12 if detail else 4)).fetchall()
                a['recent_actions'] = [action_row(dict(r), now) for r in rows]
                a['last_verified_at'] = db.execute("SELECT MAX(updated_at) FROM api_actions WHERE state='verified' AND json_extract(intent,'$.account_id')=?", (aid,)).fetchone()[0]
            out['held_tasks'] = [fields(dict(r), ('account_id', 'plan_id', 'task_id', 'action_id', 'updated_at')) for r in db.execute('SELECT * FROM api_task_holds WHERE active=1 LIMIT 101')]
            for table, kind in (('hybrid_runs', 'f5'), ('hybrid_rule_runs', 'rules')):
                for r in db.execute("SELECT * FROM " + table + " WHERE state NOT IN ('completed','cancelled') ORDER BY updated_at DESC LIMIT 101"):
                    out['open_runs'].append(run_row(dict(r), kind, now))
                for aid in accounts:
                    r = db.execute('SELECT * FROM ' + table + ' WHERE account_id=? ORDER BY updated_at DESC LIMIT 1', (aid,)).fetchone()
                    if r:
                        out['latest_runs'].append(run_row(dict(r), kind, now))
    except (OSError, ValueError, sqlite3.Error) as exc:
        out['errors'].append({'source': 'official-api-actions.db', 'error_type': type(exc).__name__})
    try:
        with connect(ROOT / 'data/official-api-rate.db') as db:
            out['rate_control']['cooldowns'] = [{**fields(dict(r), ('endpoint', 'code', 'reason', 'strikes', 'limit_kind')),
                 'until': datetime.fromtimestamp(r['until_at'], CN).isoformat(),
                 'retry_after_seconds': max(0, round(r['until_at'] - now.timestamp()))}
                 for r in db.execute('SELECT * FROM rate_breakers WHERE until_at>?', (now.timestamp(),))]
            out['rate_control']['recent_failures'] = [{**fields(dict(r), ('endpoint', 'code', 'reason', 'request_id', 'hint', 'scope', 'limit_kind')),
                 'at': datetime.fromtimestamp(r['at'], CN).isoformat()}
                 for r in db.execute('SELECT * FROM rate_failures ORDER BY id DESC LIMIT 6')]
    except (OSError, ValueError, sqlite3.Error) as exc:
        out['errors'].append({'source': 'official-api-rate.db', 'error_type': type(exc).__name__})
    out['accounts'] = sorted(accounts.values(), key=lambda a: a['id'])
    if s:
        live = {r['id']: r for r in s.get('api_actions', [])}
        for item in out['pending_actions']:
            original = live.get(item['id'], {})
            item.update(fields(original, ('readback_available', 'hold_available')))
    return clean(out)


def brief(snapshot):
    """Small routine input; use the full export only for an actual incident."""
    out = fields(snapshot, ('schema_version', 'collected_at', 'timezone', 'read_only', 'service', 'errors',
                           'source_consistency', 'pending_actions_truncated', 'pending_actions',
                           'open_runs', 'held_tasks', 'rate_control', 'execution_strategy', 'budget_recovery'))
    out['executor'] = fields(snapshot['executor'], ('provider', 'enabled', 'armed', 'current', 'busy',
                                                    'suspended', 'heartbeat_age_seconds', 'error', 'source','mode','max_parallel','account_workers','control_return_error'))
    out['accounts'] = []
    for a in snapshot['accounts']:
        p = a.get('profile', {})
        item = {**fields(a, ('id', 'name', 'scene', 'observe', 'delivery_entry', 'last_verified_at')),
                'profile': fields(p, ('revision', 'enabled', 'main_interval_seconds', 'auto_interval_seconds',
                                     'operation_start', 'operation_end', 'broadcast_protection', 'features_enabled')),
                'lanes': {k: fields(v, ('running', 'next_at', 'last_at')) for k, v in a.get('lanes', {}).items()}}
        for key in ('f5_authorization', 'rules_authorization'):
            g = a.get(key)
            item[key] = None if g is None else {**fields(g, ('plan_id', 'enabled', 'state', 'next_at', 'message')),
                     'revision_matches': g.get('profile_revision') == p.get('revision')}
        out['accounts'].append(item)
    out['rate_control']['recent_failures'] = out['rate_control'].get('recent_failures', [])[:3]
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--offline', action='store_true', help='No HTTP request; SQLite/settings only')
    p.add_argument('--detail', action='store_true', help='Include saved business rule parameters and more history')
    p.add_argument('--brief', action='store_true', help='Small routine input for the monitor model')
    p.add_argument('--timeout', type=float, default=8)
    p.add_argument('--output', type=Path, help='Write the sanitized result, not raw /api/cruise')
    args = p.parse_args()
    if not 1 <= args.timeout <= 15:
        p.error('timeout must be between 1 and 15 seconds')
    if args.brief and args.detail:
        p.error('Choose --brief or --detail')
    result = collect(offline=args.offline, detail=args.detail, timeout=args.timeout)
    if args.brief:
        result = brief(result)
    value = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        output = args.output.resolve()
        # A dedicated artifact folder prevents overwriting live settings/DBs.
        folder = (ROOT / 'data/monitoring').resolve()
        if output.parent != folder or output.suffix.lower() != '.json':
            p.error('Output must be a .json file directly inside data/monitoring')
        folder.mkdir(parents=True, exist_ok=True)
        output.write_text(value + '\n', 'utf-8')
        print(json.dumps({'output': str(output), 'service': result['service'], 'accounts': len(result['accounts']), 'errors': result['errors']}, ensure_ascii=False))
    else:
        print(value)


if __name__ == '__main__':
    main()
