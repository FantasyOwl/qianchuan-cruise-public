"""Single creative's declared share of an explicitly linked plan ad ledger.

This is descriptive monitoring, not a complete creative inventory, platform
certification, allocation recommendation or permission to change an account.
"""
from .engine import _time


def _context(row):
    value = row['raw'].get('context', {})
    return value if isinstance(value, dict) else {}


def _cost_conflict(records, last, field):
    # Do not select one favourable value from duplicate/corrected observations.
    values = {r['metrics'].get(field) for r in records if r['data'] == last['data']}
    if len(values) > 1:
        return True
    current = last['metrics'].get(field)
    return current is not None and any(r['data'] is not None and r['data'] < last['data']
                                      and r['metrics'].get(field) is not None
                                      and r['metrics'][field] > current for r in records)


def observe_creative_share(records, parent_groups, rules, now, observation_status):
    last = max(records, key=lambda r: r['captured'])
    raw, context, source = last['raw'], _context(last), last['source']
    parent_id, parent_key = context.get('parent_plan_id'), context.get('parent_source_key')
    parent_id = parent_id if isinstance(parent_id, str) and 0 < len(parent_id) <= 150 else None
    parent_key = parent_key if isinstance(parent_key, str) and 0 < len(parent_key) <= 1000 else None
    ledger = context.get('spend_ledger')
    ledger = ledger if isinstance(ledger, str) else 'unknown'
    value = {'parent_plan_id': parent_id, 'parent_source_key': parent_key, 'parent_snapshot_id': None,
             'ledger': ledger if ledger in {'base', 'overall'} else 'unknown',
             'creative_spend': None, 'parent_spend': None, 'share_percent': None,
             'creative_data_at': source.get('data_at'), 'parent_data_at': None,
             'status': 'missing', 'is_current': False, 'reason': '',
             'synthetic': source.get('kind') == 'demo', 'platform_verified': False,
             'complete_creative_coverage_verified': False, 'used_for_action': False,
             'note': '单素材广告消耗占母计划同范围消耗的比例；不表示素材全集覆盖、ROI、利润或调控资格。'}

    def stop(reason, status='missing'):
        value.update(reason=reason, status=status)
        return value

    if (context.get('creative_parent_verified') is not True or not parent_id or not parent_key
            or source.get('kind') not in {'import', 'demo'}):
        return stop('缺少明确核对的母计划关系和精确来源；页面标签或素材名称不能证明归属')
    if ledger not in {'base', 'overall'}:
        return stop('缺少明确的基础或总体广告消耗台账')
    field = 'base_spend' if ledger == 'base' else 'spend'
    peers = [r for r in records if r['captured'] == last['captured']]
    identity = lambda r: tuple(repr(_context(r).get(k)) for k in
                              ('parent_plan_id', 'parent_source_key', 'creative_parent_verified', 'spend_ledger'))
    if len({identity(r) for r in peers}) != 1:
        return stop('同次素材采集的母计划关系或消耗台账存在矛盾', 'conflict')
    if source.get('period_start') is not None and _time(source['period_start']) is None:
        return stop('素材统计期间起点无效，不能计算占比', 'unverified')
    candidates = []
    for group in parent_groups.get((raw['account_id'], parent_id, parent_key), []):
        parent = max(group, key=lambda r: r['captured'])
        if parent['source'].get('period_start') is not None and _time(parent['source']['period_start']) is None:
            continue
        if (parent['date'] == last['date']
                and parent['source'].get('metric_period', 'day') == source.get('metric_period', 'day')
                and _time(parent['source'].get('period_start')) == _time(source.get('period_start'))):
            candidates.append((parent, group))
    if len(candidates) != 1:
        return stop('未找到唯一的同账户、同日期或同场次母计划来源')
    parent, parent_records = candidates[0]
    parent_raw, parent_context = parent['raw'], _context(parent)
    value.update(parent_snapshot_id=parent_raw.get('id'), parent_data_at=parent['source'].get('data_at'))
    parent_heads = [r for r in parent_records if r['captured'] == parent['captured']]
    if len({tuple(repr(_context(r).get(k)) for k in ('plan_id', 'spend_ledger', 'report_start_at', 'report_end_at'))
            for r in parent_heads}) != 1:
        return stop('同次母计划采集的对象或消耗统计范围存在矛盾', 'conflict')
    if (raw.get('scene') != parent_raw.get('scene') or raw.get('promotion_type') != parent_raw.get('promotion_type')
            or raw.get('promotion_type') not in {'universal', 'chengfang', 'standard', 'search'}
            or source.get('kind') != parent['source'].get('kind')):
        return stop('素材与母计划的场景、产品或证据类型不一致', 'conflict')
    if parent_context.get('spend_ledger') != ledger:
        return stop('素材与母计划未明确使用同一广告消耗台账', 'conflict')
    for name in ('report_start_at', 'report_end_at'):
        a, b = context.get(name), parent_context.get(name)
        if ((a is not None and _time(a) is None) or (b is not None and _time(b) is None)
                or _time(a) != _time(b)):
            return stop('素材与母计划的报告查询边界不一致', 'conflict')
    if last['data'] is None or last['data'] != parent['data']:
        return stop('素材与母计划的指标更新时间不一致，不能把不同累计截点直接相除', 'unverified')
    states = [observation_status(records, peer, rules, now) for peer in peers]
    states += [observation_status(parent_records, peer, rules, now) for peer in parent_heads]
    if any(state == 'unverified' for state, _ in states):
        return stop('素材或母计划最新采集的时间、期间或数据质量待核验', 'unverified')
    if _cost_conflict(records, last, field) or _cost_conflict(parent_records, parent, field):
        return stop('同一指标时刻消耗矛盾或日内累计回退，不能选择有利值计算占比', 'conflict')
    amounts = [last['metrics'].get(field), parent['metrics'].get(field)]
    value.update(creative_spend=amounts[0], parent_spend=amounts[1])
    if any(amount is None for amount in amounts):
        return stop('缺少所声明台账的独立素材消耗或母计划消耗，不借用其他台账')
    if amounts[1] <= 0:
        return stop('母计划同范围消耗为零，不能计算占比')
    if amounts[0] > amounts[1]:
        return stop('素材消耗大于母计划同范围消耗，先核对归属与统计范围', 'conflict')
    current = all(state == 'fresh' for state, _ in states)
    value.update(share_percent=amounts[0]/amounts[1]*100, is_current=current,
                 status='observed' if current else 'historical',
                 reason='同一指标截点的已声明关联消耗比值' if current else '仅保留历史比值，素材或母计划当前时效不满足')
    return value
