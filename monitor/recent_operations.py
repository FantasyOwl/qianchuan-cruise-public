"""Conservative plan cooldowns shared across observation sources.

An observed change can withhold a proposal. It cannot certify complete history,
an execution acknowledgement, or the end of another restriction.
"""
from datetime import timedelta

from .engine import _record, _settings, _time
from .plan_operation_observation import plan_operations_from_snapshot


TUNING_ACTIONS = {'budget_up', 'budget_down', 'control_budget_up', 'roi_target_up',
    'roi_target_down', 'bid_down', 'bid_restore', 'create_chase', 'one_click_boost',
    'one_click_control', 'resume_plan'}


def apply_recent_operation_guard(result, snapshots, settings, now):
    rules, accounts = _settings(settings)
    changes, origins, logs, conflicts = {}, {}, {}, set()
    for raw in snapshots if isinstance(snapshots, list) else []:
        try:
            record = _record(raw, accounts, now)
        except (TypeError, ValueError, AttributeError, OverflowError):
            record = None
        if record is None:
            continue
        row = record['raw']; source = record['source']; account = row['account_id']
        synthetic = source['kind'] == 'demo'
        origins[(account, row['entity_id'], row['scope'], ':'.join(map(str, record['key'][3:])))] = synthetic
        if row['scope'] == 'plan':
            plan = row['entity_id']
        elif row['scope'] == 'control':
            context = row.get('context', {})
            plan = context.get('parent_plan_id') if isinstance(context, dict) else None
            if not isinstance(plan, str) or not plan or source.get('parent_entity_id') != plan:
                continue
        else:
            continue
        try:
            operations = plan_operations_from_snapshot(row)
        except (TypeError, ValueError, AttributeError, OverflowError):
            operations = []
        for operation in operations:
            identity = (account, plan, synthetic, operation['occurred_at'], operation['kind'], operation['control_id'])
            if identity in logs and logs[identity]['operation'] != operation:
                conflicts.add(identity)
            if identity not in logs or record['captured'] > logs[identity]['captured']:
                logs[identity] = {'operation': operation, 'captured': record['captured'],
                    'snapshot_id': row.get('id'), 'source_key': source.get('series_key')}
        changed = _time(record['control'].get('last_change_at'))
        if changed is None or changed > now or changed > record['captured']:
            continue
        # Keep the longest supported tuning interval; each action uses its own
        # interval below. Crossing midnight must not erase a recent operation.
        if now - changed >= timedelta(minutes=max(120, rules['cooldown_minutes'])):
            continue
        key = (account, plan, synthetic)
        if key not in changes or changed > changes[key]['at']:
            changes[key] = {'at': changed, 'snapshot_id': row.get('id'),
                'source_key': source.get('series_key'), 'history_complete': False}

    observations = []
    for (account, plan, synthetic), change in sorted(changes.items()):
        observations.append({'account_id': account, 'plan_id': plan,
            'last_observed_change_at': change['at'].isoformat(),
            'general_tuning_after': (change['at'] + timedelta(minutes=rules['cooldown_minutes'])).isoformat(),
            'bid_tuning_after': (change['at'] + timedelta(minutes=max(120, rules['cooldown_minutes']))).isoformat(),
            'snapshot_id': change['snapshot_id'], 'source_key': change['source_key'],
            'synthetic': synthetic, 'history_complete': False,
            'note': '仅保留已观察变更的时间下界；到时不代表历史、保障、数据或执行资格已齐备。'})
    result['recent_plan_operations'] = observations
    result['observed_plan_operations'] = [
        {'account_id': identity[0], 'plan_id': identity[1], 'synthetic': identity[2],
         **entry['operation'], 'observed_at': entry['captured'].isoformat(),
         'snapshot_id': entry['snapshot_id'], 'source_key': entry['source_key'],
         'history_complete': False, 'execution_verified': False}
        for identity, entry in sorted(logs.items(), reverse=True) if identity not in conflicts]
    result['conflicting_plan_operation_observations'] = len(conflicts)

    for recommendation in result.get('recommendations', []):
        if recommendation.get('action') not in TUNING_ACTIONS:
            continue
        identity = (recommendation.get('account_id'), recommendation.get('entity_id'),
                    recommendation.get('scope'), recommendation.get('source_key'))
        if identity not in origins:
            continue
        parameters = recommendation.get('parameters', {})
        plan = (parameters.get('parent_plan_id') if isinstance(parameters, dict) else None)
        plan = plan or recommendation.get('target_id') or recommendation.get('entity_id')
        change = changes.get((recommendation['account_id'], plan, origins[identity]))
        if change is None:
            continue
        minutes = max(120 if recommendation['action'] in {'bid_down', 'bid_restore'} else 60,
                      rules['cooldown_minutes'])
        until = change['at'] + timedelta(minutes=minutes)
        if now >= until:
            continue
        recommendation['eligible'] = False
        recommendation['blockers'].append('同一计划其他观察来源记录了近期变更，尚未满足共同操作间隔')
        recommendation.setdefault('evidence', {})['observed_plan_cooldown'] = {
            'last_change_at': change['at'].isoformat(), 'not_before': until.isoformat(),
            'snapshot_id': change['snapshot_id'], 'history_complete': False}
    return result
