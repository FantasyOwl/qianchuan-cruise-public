"""Inspect declared standard-plan bid history without platform IO.

Consistent imported observations are evidence for an offline strategy, not
authenticated platform configuration or proof that a bid change caused a stall.
"""
from decimal import Decimal
from datetime import timedelta

from .engine import _number, _time


def inspect_standard_bid(records, rules, now):
    last = records[-1]
    raw, metrics = last['raw'], last['metrics']
    context = raw.get('context', {})
    current = metrics.get('bid')
    config_missing = []
    if context.get('plan_bid_mode') != 'conversion_cost':
        config_missing.append('明确标准计划采用转化成本出价，不能把ROI设置当作bid')
    if context.get('plan_bid_config_verified') is not True:
        config_missing.append('context.plan_bid_config_verified=true的明确配置声明')
    observed = _time(context.get('plan_bid_observed_at'))
    if (observed is None or not -120 <= (now-observed).total_seconds() <= rules['max_age_seconds']
            or abs((last['captured']-observed).total_seconds()) > 300):
        config_missing.append('独立且新鲜的出价配置观察时间，不能用统计日期代替')
    if current is None or current <= 0:
        config_missing.append('明确正数当前出价')
    if raw.get('source', {}).get('kind') not in {'import', 'demo'} or raw.get('source', {}).get('coverage') != 'report':
        config_missing.append('完整范围的明确报告；页面可见值不能认证出价配置')

    points = {}
    history_missing = []
    kind = raw.get('source', {}).get('kind')
    for record in records:
        item = record['raw']; ctx = item.get('context', {})
        at = _time(ctx.get('plan_bid_observed_at'))
        bid = record['metrics'].get('bid')
        if (ctx.get('plan_bid_config_verified') is not True or ctx.get('plan_bid_mode') != 'conversion_cost'
                or item.get('source', {}).get('kind') != kind or item.get('source', {}).get('coverage') != 'report'
                or at is None or bid is None or bid <= 0 or record['issues']
                or abs((record['captured']-at).total_seconds()) > 300 or at > now+timedelta(seconds=120)):
            # An unknown configuration inside a comparison interval is a gap,
            # not permission to carry the preceding bid forward.
            at = at if at is not None else record['data']
            bid = None
        if at is None:
            history_missing.append('出价历史缺少可定位的配置观察时钟')
            continue
        point = {'at': at, 'bid': bid, 'snapshot_ids': [item.get('id')]}
        previous = points.get(at)
        if previous is not None:
            point['snapshot_ids'] = previous['snapshot_ids']+point['snapshot_ids']
            if previous['bid'] != bid:
                point['bid'] = None
        points[at] = point
    points = sorted(points.values(), key=lambda p: p['at'])
    gap = timedelta(minutes=rules['max_gap_minutes'])

    def interval(start, end, expected, *, end_exclusive=False):
        selected = [p for p in points if start-timedelta(minutes=5) <= p['at']
                    and (p['at'] < end if end_exclusive else p['at'] <= end)]
        missing = []
        if not selected or selected[0]['at'] > start+timedelta(minutes=5):
            missing.append('出价序列缺少观察窗口起点')
        if not selected or end-selected[-1]['at'] > gap:
            missing.append('出价序列缺少观察窗口终点')
        if any(b['at']-a['at'] > gap for a, b in zip(selected, selected[1:])):
            missing.append('出价配置采样间隔超出上限')
        if any(p['bid'] is None or p['bid'] != expected for p in selected):
            missing.append('窗口内出价未知、冲突或发生了其他变更')
        return selected, missing

    stable, stable_missing = interval(now-timedelta(hours=2), now, current)
    tuning_missing = list(config_missing)+list(history_missing)+stable_missing
    if context.get('target_cost_basis') != last['basis']:
        tuning_missing.append('目标成本与当前考核台账口径一致')

    prior = metrics.get('bid_before_drop')
    other = metrics.get('previous_bid')
    if prior is not None and other is not None and prior != other:
        history_missing.append('bid_before_drop与previous_bid相互冲突')
    prior = prior if prior is not None else other
    changed = _time(context.get('last_bid_change_at'))
    recovery_missing = list(config_missing)+list(history_missing)
    if context.get('bid_change_history_known') is not True:
        recovery_missing.append('明确完整的出价变更历史声明')
    if (current is None or prior is None or current <= 0 or prior <= current
            or not Decimal('.05') <= 1-Decimal(str(current))/Decimal(str(prior)) <= Decimal('.1')):
        recovery_missing.append('可核验的上次5%至10%降价前后出价')
    before, after = [], []
    if changed is None or changed > now or now-changed < timedelta(hours=1):
        recovery_missing.append('降价时点及降价后至少一个完整小时')
    else:
        before, missing_before = interval(changed-timedelta(hours=1), changed, prior, end_exclusive=True)
        after, missing_after = interval(changed, now, current)
        recovery_missing += ['降价前：'+m for m in missing_before]+['降价后：'+m for m in missing_after]
        if not after or abs((after[0]['at']-changed).total_seconds()) > 300:
            recovery_missing.append('首个降价后配置观察与声明时点不吻合')
    def public(values):
        return [{'at': p['at'].isoformat(), 'bid': p['bid'], 'snapshot_ids': p['snapshot_ids']} for p in values]
    return {'account_id': raw['account_id'], 'entity_id': raw['entity_id'], 'latest_snapshot_id': raw.get('id'),
        'source_key': raw.get('source', {}).get('series_key'), 'observed_bid': current, 'prior_bid': prior,
        'changed_at': changed.isoformat() if changed is not None else None,
        'tuning_missing': list(dict.fromkeys(tuning_missing)), 'recovery_missing': list(dict.fromkeys(recovery_missing)),
        'stable_two_hours': not tuning_missing, 'last_reduction_observed': not recovery_missing,
        'stable_observations': public(stable), 'before_observations': public(before), 'after_observations': public(after),
        'evidence_kind': 'provided_report_declarations', 'platform_configuration_authenticated': False,
        'causality_proven': False, 'native_bid_submission_available': False}
