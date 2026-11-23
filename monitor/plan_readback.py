"""Compare a supplied list response offline; never certify provenance or unlock IO.

The pinned public SDK predates the 2026-10-20 list API change. Only its
configuration response shape is used, never its retired query or stats fields.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, DecimalException

from .actuation import amount, exact_id


SDK_REVISION = '1907e5f46b1b138062ff017a2325c38787ed6bca'
LIST_PATH = '/open_api/v1.0/qianchuan/uni_promotion/list/'
GOALS = frozenset({'LIVE_PROM_GOODS', 'VIDEO_PROM_GOODS'})
CONTEXT_KEYS = frozenset({'account_id', 'marketing_goal', 'request_started_at', 'received_at'})


def _clock(value):
    try:
        result = datetime.fromisoformat(value) if isinstance(value, str) else value
        if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise ValueError('核对时间必须含明确时区') from None


def _id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('响应计划ID必须是无损整数或十进制字符串')
    text = str(value)
    number = exact_id(text)
    if str(number) != text:
        raise ValueError('响应计划ID不是规范十进制值')
    return text


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('响应含重复JSON字段')
        compact = ''.join(c for c in key.lower() if c.isalnum())
        if any(word in compact for word in ('token', 'authorization', 'cookie', 'secret', 'password', 'apikey')):
            raise ValueError('只接受业务响应，不接受凭据或请求头')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('响应含非有限数值')


def _business_data(raw):
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > 1024 * 1024:
        raise ValueError('业务响应须为不超过1MiB的JSON文本')
    try:
        response = json.loads(raw, parse_float=Decimal, object_pairs_hook=_pairs,
                              parse_constant=_invalid_constant)
    except (ValueError, RecursionError, DecimalException):
        raise ValueError('业务响应JSON无效、含重复字段或凭据') from None
    if not isinstance(response, dict) or type(response.get('code')) is not int or response['code'] != 0:
        raise ValueError('业务响应未明确成功')
    data = response.get('data')
    if not isinstance(data, dict):
        raise ValueError('业务响应缺少数据对象')
    return data


def _decode(raw):
    data = _business_data(raw)
    if not isinstance(data.get('ad_list'), list):
        raise ValueError('业务响应缺少计划列表')
    plans = {}
    for item in data['ad_list']:
        info = item.get('ad_info') if isinstance(item, dict) else None
        if not isinstance(info, dict):
            raise ValueError('计划列表结构不完整')
        entity_id = _id(info.get('id'))
        if entity_id in plans:
            raise ValueError('响应含重复计划ID，不能选择有利记录')
        plans[entity_id] = info
    return plans


def compare_supplied_readback(raw, query_context, attempt, now):
    """Return a minimal untrusted comparison, not an applied-action event.

    `attempt` is loaded from the disconnected submission ledger by its caller.
    Request metadata is supplied locally: the list envelope itself does not
    establish account identity or a server observation timestamp.
    """
    return _compare_values(raw, query_context, attempt, now, _decode(raw))


def _compare_values(raw, query_context, attempt, now, plans, *, detail=False):
    if not isinstance(query_context, dict) or set(query_context) != CONTEXT_KEYS:
        raise ValueError('须提供且只提供账户、目标及查询起止时间')
    if not isinstance(attempt, dict) or not isinstance(attempt.get('context'), dict):
        raise ValueError('提交记录缺少对象证据')
    context = attempt['context']
    account_id = _id(query_context['account_id'])
    entity_id = _id(context.get('entity_id'))
    if not isinstance(query_context['marketing_goal'], str) or query_context['marketing_goal'] not in GOALS:
        raise ValueError('查询推广目标无效')
    now, started, received, submitted = map(_clock, (
        now, query_context['request_started_at'], query_context['received_at'], attempt.get('created_at')))
    if not submitted <= started <= received <= now:
        raise ValueError('查询时间必须在提交后且不得位于未来')
    if (now-received).total_seconds() > 600 or (received-started).total_seconds() > 600:
        raise ValueError('提供的查询已过期或耗时无法确认')
    if account_id != context.get('account_id'):
        raise ValueError('查询账户与提交记录不一致')
    action = context.get('action')
    if not isinstance(action, str) or action not in {'budget_up', 'budget_down', 'pause_plan', 'resume_plan', 'roi_target_up', 'roi_target_down'}:
        raise ValueError('提交记录不是可核对的计划预算、ROI或启停动作')
    expected_goal = context.get('marketing_goal')
    result = {
        'account_id': account_id, 'entity_id': entity_id, 'action': action,
        'status': 'inconclusive', 'expected': None, 'observed': None,
        'blockers': [], 'source': 'supplied_plan_list_response',
        'response_digest': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
        'query_context': {**query_context, 'request_started_at': started.isoformat(), 'received_at': received.isoformat()},
        'sdk_revision': SDK_REVISION, 'api_path': LIST_PATH,
        'provenance_verified': False, 'platform_state_observed': False,
        'actual_delivery_confirmed': False, 'execution_confirmed': False,
        'can_release_hold': False, 'automatic_retry': False,
        'checked_at': now.isoformat(),
    }
    if detail:
        result.update(source='supplied_plan_detail_response',api_path='/open_api/v1.0/qianchuan/uni_promotion/ad/detail/',
                      contract_source='https://open.oceanengine.com/labels/12/docs/1804362305657868',
                      contract_verified_at='2026-10-23')
        result.pop('sdk_revision')
    errors = result['blockers']
    if not isinstance(expected_goal, str) or expected_goal not in GOALS:
        errors.append('原提交记录缺少明确推广目标')
    elif expected_goal != query_context['marketing_goal']:
        errors.append('查询推广目标与提交记录不一致')
    info = plans.get(entity_id)
    if info is None:
        # A filtered/paginated list cannot establish deletion or a failed write.
        errors.append('当前响应未包含目标计划，不能推断其不存在或操作失败')
        return result
    if info.get('marketing_goal') != query_context['marketing_goal']:
        errors.append('响应计划的推广目标不明确或不一致')
    if detail and (type(info.get('adlab_scene')) is not int or info['adlab_scene'] not in {0, 1}):
        errors.append('响应未明确为全域或乘方计划')
    if action in {'budget_up', 'budget_down'}:
        if info.get('budget_mode') != 'BUDGET_MODE_DAY':
            errors.append('仅核对明确日预算，不能混用总预算或不限预算')
        try:
            before, expected, observed = amount(context.get('before')), amount(context.get('after')), amount(info.get('budget'))
            result.update(expected=format(expected, '.2f'), observed=format(observed, '.2f'))
            if (action == 'budget_up' and expected <= before) or (action == 'budget_down' and expected >= before):
                errors.append('原提交记录的预算方向不一致')
        except (ValueError, DecimalException):
            errors.append('提交目标或响应预算不是明确的两位小数金额')
    elif action in {'roi_target_up', 'roi_target_down'}:
        goals = {'overall_payment':'AD_CONVERT_TYPE_LIVE_PAY_ROI','net':'AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI'}
        basis = context.get('metric_basis')
        expected_optimization = goals.get(basis) if isinstance(basis, str) else None
        if not detail:
            errors.append('ROI核对只采用当前已核验的计划详情合同')
        if (expected_optimization is None or context.get('plan_optimization_goal') != expected_optimization
                or info.get('deep_external_action') != expected_optimization):
            errors.append('原提交及详情的ROI优化目标未明确匹配')
        if context.get('plan_delivery_mode') != 'cost_control' or info.get('smart_bid_type') != 'SMART_BID_CUSTOM':
            errors.append('原提交或响应计划未明确为控成本模式')
        try:
            before, expected, observed = amount(context.get('before')), amount(context.get('after')), amount(info.get('roi2_goal'))
            result.update(expected=format(expected, '.2f'), observed=format(observed, '.2f'))
            fraction = (expected-before)/before
            if (not Decimal('.05') <= abs(fraction) <= Decimal('.10')
                    or fraction*(1 if action=='roi_target_up' else -1) <= 0):
                errors.append('原提交记录的ROI方向或幅度不一致')
        except (ValueError, DecimalException):
            errors.append('提交目标或响应ROI不是明确的两位小数正数')
    elif action in {'pause_plan', 'resume_plan'}:
        expected_after = action == 'resume_plan'
        if context.get('after') is not expected_after or context.get('before') is not (not expected_after):
            errors.append('原提交记录的启停方向不明确')
        result['expected'] = 'ENABLE' if expected_after else 'DISABLE'
        observed = info.get('opt_status')
        if not isinstance(observed, str) or observed not in {'ENABLE', 'DISABLE', 'DELETE', 'QUOTA_DISABLE', 'ROI2_DISABLE'}:
            errors.append('响应没有可识别的操作开关状态')
        else:
            result['observed'] = observed
    else:
        errors.append('当前核对只支持计划预算和启停提交')
    if not errors:
        result['status'] = 'matches_requested_value' if result['expected'] == result['observed'] else 'differs_requested_value'
    return result
