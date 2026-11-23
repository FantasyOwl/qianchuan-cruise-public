"""Three-hour growth stability guardrails, not official platform limits."""
import math
from datetime import datetime


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def assess_growth_stability(windows, rules):
    limits = {'roi': rules['growth_roi_max_ratio'], 'conversion_cost': rules['growth_cost_max_ratio'],
              'spend_rate': rules['growth_spend_max_ratio']}
    result = {'state': 'insufficient', 'passed': False, 'policy_basis': 'local_strategy_guardrail',
              'thresholds': limits, 'ratios': {}, 'windows': [], 'blockers': [], 'platform_policy_verified': False}
    if len(windows) != 3 or not all(w.get('complete') is True for w in windows):
        result['blockers'].append('稳定放量需要三个连续完整小时')
        return result
    previous_end = None
    ledgers = set()
    for w in windows:
        try:
            start = datetime.fromisoformat(w['observed_start'])
            end = datetime.fromisoformat(w['observed_end'])
            seconds = (end-start).total_seconds()
            if (start.tzinfo is None or end.tzinfo is None or not 3300 <= seconds <= 3900
                    or w.get('observed_seconds') != seconds or previous_end is not None and start != previous_end):
                raise ValueError('incomplete boundaries')
        except (KeyError, TypeError, ValueError, OverflowError):
            result['blockers'].append('三小时实际采样边界缺失、不连续或时长不完整')
            return result
        previous_end = end
        if (not all(_positive(w.get(k)) for k in ('spend', 'roi', 'conversion_cost', 'orders'))
                or w['orders'] < rules['min_orders'] or w['spend'] < rules['min_spend']):
            result['blockers'].append('三个小时均需可计算ROI、转化成本和达标的真实订单/消耗样本；零订单止损样本不能代替放量样本')
            return result
        ledgers.add(w.get('ledger'))
        result['windows'].append({'observed_start': w['observed_start'], 'observed_end': w['observed_end'],
                                  'observed_seconds': seconds, 'spend': w['spend'], 'orders': w['orders'],
                                  'spend_rate_per_hour': w['spend']*3600/seconds,
                                  'roi': w['roi'], 'conversion_cost': w['conversion_cost'], 'ledger': w.get('ledger')})
    if len(ledgers) != 1 or not ledgers.issubset({'base', 'overall'}):
        result['blockers'].append('稳定放量的三个小时必须使用同一基础或总体台账')
        return result
    result['total_observed_seconds'] = sum(w['observed_seconds'] for w in result['windows'])
    if result['total_observed_seconds'] < 3*3600:
        result['blockers'].append('实际连续观察未满三小时，采样边界容差不能补足观察时长')
        return result
    for field, label in (('roi', 'ROI'), ('conversion_cost', '转化成本'), ('spend_rate', '实际小时消耗速度')):
        values = [w['spend_rate_per_hour' if field == 'spend_rate' else field] for w in result['windows']]
        ratio = max(values)/min(values)
        result['ratios'][field] = ratio
        if ratio > limits[field]+1e-12:
            result['blockers'].append(f'三小时{label}最高/最低值超过本地稳定性上限{limits[field]:g}')
    result.update(state='unstable' if result['blockers'] else 'stable', passed=not result['blockers'])
    return result
