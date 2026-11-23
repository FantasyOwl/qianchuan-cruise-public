"""Cost-guarantee observations and action checks; never a payout certificate.

Pure local code. Product identity is explicit evidence, never inferred from an
account name, URL, or ROI column. ``allowed`` means these limited action checks
passed; it does not replace account identity, human approval, or queue guards.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

CN = timezone(timedelta(hours=8))
RULE_VERSION = '2026-10-26'
SOURCES = [
    {'id': 'standard_cost', 'title': '巨量千川标准投放成本保障规则',
     'url': 'https://school.jinritemai.com/doudian/web/articlev0/aJq5ye7vuAih',
     'checked_at': RULE_VERSION, 'coverage': '2026-07-01修订；公开检索正文可读，直接页面未返回正文'},
    {'id': 'search_first_screen', 'title': '直播全域搜索抢首屏调控产品手册',
     'url': 'https://school.jinritemai.com/doudian/web/articlev0/aJpoFWfBatrU',
     'checked_at': RULE_VERSION, 'coverage': '独立调控任务的公开手册；最终以适用规则为准'},
    {'id': 'chengfang_faq', 'title': '千川乘方基础投放FAQ',
     'url': 'https://school.jinritemai.com/doudian/web/article/aJq5t8zZjhr5',
     'checked_at': RULE_VERSION, 'coverage': '公开检索正文；ROI口径与放量追投边界'},
    {'id': 'live_manual', 'title': '巨量千川直播全域投放产品手册（简洁版）',
     'url': 'https://school.jinritemai.com/doudian/web/article/aJpvpMFYyQzM',
     'checked_at': RULE_VERSION, 'coverage': '2026-09-02更新；起量与控量不同；不外推到所有追投模式'},
    {'id': 'product_course', 'title': '中小商家千川广告投放运营基础及工具使用',
     'url': 'https://school.jinritemai.com/doudian/web/course-series/cJZHjLnhaLyw/cJZHjLnhaLywlJZJUawr8APt',
     'checked_at': RULE_VERSION, 'coverage': '商品全域支付ROI教学示例；非当前完整规则替代品'},
    {'id': 'violations', 'title': '巨量千川成本保障规则之违规处置细则',
     'url': 'https://school.jinritemai.com/doudian/web/articlev0/aJqNuY64FRZf',
     'checked_at': RULE_VERSION, 'coverage': '2026-04-28生效，2026-09-10发布；异常操作与不合理批量/频次风险'},
    {'id': 'gmv_definition', 'title': '巨量千川支付GMV口径升级产品手册',
     'url': 'https://school.jinritemai.com/doudian/web/article/aJq1dJMYa7R3',
     'checked_at': RULE_VERSION, 'coverage': '2026-07口径；短时退款和异常交易须按平台统计剔除'},
    {'id': 'all_domain_rules', 'title': '巨量千川规则中心全域投放成本保障规则',
     'url': 'https://qianchuan.jinritemai.com/support/content/139336?graphId=395&pageId=229',
     'checked_at': RULE_VERSION, 'coverage': '公开入口未返回完整正文；全域及乘方当前完整条款仍未独立核验'},
]
_SOURCE = {s['id']: s for s in SOURCES}
_SUBMITTED = {'verified', 'executed', 'submitted', 'reconciled_target', 'reconciled_other'}
_ROI = {'roi_target_up', 'roi_target_down', 'control_roi_up', 'control_roi_down'}
_BID = _ROI | {'bid_up', 'bid_down', 'bid_change'}
_TARGETING = {'targeting_change', 'targeting_update'}
_NON_ROI_GOALS = {'conversion_cost', 'purchase', 'live_purchase', 'product_purchase',
                  'live_settlement', 'orders', 'click', 'impressions'}
_LABELS = {'unknown': '保障条件未核实', 'not_applicable': '此部分不适用成本保障',
           'conditions_not_met': '已知保障条件不满足', 'pending_platform': '等待平台核算',
           'estimated_only': '仅按已提供条件预估'}


def _time(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    else:
        return None
    return dt.astimezone(CN) if dt.tzinfo is not None else None


def _number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = Decimal(str(value))
        return v if v.is_finite() and v >= 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _flatten(value):
    facts = {k: v for k, v in value.items() if k not in {'context', 'control', 'metrics', 'source'}}
    conflicts = []
    for section in ('context', 'control'):
        child = value.get(section, {})
        if not isinstance(child, dict):
            continue
        for key, val in child.items():
            if key in facts and facts[key] != val:
                conflicts.append(key)
                facts[key] = None
            else:
                facts[key] = val
    return facts, conflicts


def _same_entity(row, action):
    if not action.get('account_id') or row.get('account_id') != action['account_id']:
        return False
    if action.get('task_id'):
        return (row.get('task_id', row.get('entity_id')) == action['task_id']
                and row.get('parent_plan_id', row.get('plan_id')) == action.get('plan_id'))
    if row.get('task_id') or row.get('parent_plan_id'):
        return False
    return row.get('plan_id', row.get('entity_id')) == action.get('plan_id') and bool(action.get('plan_id'))


def evaluate_cost_guarantee(context=None, action=None, history=None, *, now=None):
    """Return limited eligibility observations and prospective action checks.

    ``context`` accepts a canonical snapshot or flat server-side facts. See the
    companion document for optional facts. ``action`` is an immutable queue
    intent; history is account/entity-scoped again here even if already scoped
    by its caller. The assessment target is deliberately not used as the plan's
    bidding setting. Unknown guarantee facts remain warnings for manual actions.

    Known applicable frequency limits are protected by a local product choice:
    exceeding them blocks the action to preserve benefits, not because the
    platform forbids advertising without those benefits.
    """
    if context is None:
        context = {}
    if not isinstance(context, dict) or action is not None and not isinstance(action, dict):
        raise ValueError('保障检查需要对象形式的上下文和调控单')
    if history is not None and not isinstance(history, (list, tuple)):
        raise ValueError('操作历史须为记录列表')
    clock = _time(now or datetime.now(timezone.utc))
    if clock is None:
        raise ValueError('保障检查时间须包含明确时区')
    action = action or {}
    facts, conflicts = _flatten(context)
    source = context.get('source', {})
    source_kind = source.get('kind') if isinstance(source, dict) else None
    if source_kind in {'browser_dom', 'legacy_db', 'demo'}:
        # A visible page (or demonstration) cannot certify complete mutation
        # history, a true day-opening budget, or finalized filtered statistics.
        facts['history_known'] = False
        facts['day_initial_budget_verified'] = False
        if isinstance(facts.get('guarantee_conditions'), dict):
            facts['guarantee_conditions'] = {**facts['guarantee_conditions'],
                'statistics_finalized': False, 'platform_filtered_statistics': False}
    warnings, blockers, checks = [], [], []
    source_ids = {'violations', 'all_domain_rules'}

    def check(code, outcome, message, origin, source=None):
        item = {'id': code, 'status': outcome, 'message': message, 'origin': origin}
        if source:
            item['source_id'] = source
            source_ids.add(source)
        checks.append(item)
        if outcome == 'fail':
            blockers.append(message)
        elif outcome == 'unknown':
            warnings.append(message)

    family = facts.get('product_family', 'unknown')
    if family not in {'all_domain', 'chengfang', 'standard'}:
        family = 'unknown'
    scene = facts.get('scene', 'unknown')
    if scene not in {'live', 'product'}:
        scene = 'unknown'
    # ROI label alone does not prove family, and a detail URL proves neither.
    tool = facts.get('tool', 'unknown')
    control_type = facts.get('control_type')
    if tool == 'unknown' and control_type == 'one_click_boost':
        tool = 'smart_boost'
    elif tool == 'unknown' and control_type == 'one_click_control':
        tool = 'speed_limit'
    elif tool == 'unknown' and control_type == 'material_chase':
        tool = {'cost_control': 'material_chase_cost', 'volume': 'material_chase_volume'}.get(
            facts.get('control_delivery_mode'), 'unknown')
    delivery = facts.get('delivery_mode', facts.get('plan_delivery_mode', 'unknown'))
    goal = facts.get('optimization_goal', facts.get('plan_optimization_goal', 'unknown'))
    scope = {'product_family': family, 'scene': scene, 'tool': tool,
             'delivery_mode': delivery, 'optimization_goal': goal}
    status = 'unknown'
    if conflicts:
        warnings.append('上下文存在冲突字段，不能据此确认保障：' + '、'.join(sorted(set(conflicts))))
    # Context identities must agree; absence is left to the queue identity guard.
    for key in ('account_id', 'plan_id', 'task_id'):
        if facts.get(key) is not None and action.get(key) is not None and facts[key] != action[key]:
            check('identity_' + key, 'fail', '保障上下文与调控单的账户、计划或任务不一致', 'local_identity_guard')

    excluded = False
    if tool == 'smart_boost' and family in {'all_domain', 'chengfang', 'standard'}:
        excluded = True
        source_ids.add('standard_cost' if family == 'standard' else 'live_manual' if family == 'all_domain' else 'chengfang_faq')
    elif tool == 'material_chase_volume' and family in {'all_domain', 'chengfang'}:
        excluded = True
        source_ids.add('chengfang_faq')
    elif family == 'standard' and delivery == 'volume':
        excluded = True
        source_ids.add('standard_cost')
    if excluded:
        status = 'not_applicable'
        warnings.append('已识别的起量或放量部分不按控成本保障核算；确认操作不代表获得保障')
    elif tool in {'material_chase_cost', 'speed_limit'}:
        warnings.append('控成本追投或控量需按对应工具规则核算，不能套用放量追投结论或母计划保障')
    if family == 'unknown':
        warnings.append('尚未明确识别全域、乘方或标准投放产品，未套用其他产品的保障条件')

    # A native badge is observational evidence, not a final payment certificate.
    platform = facts.get('platform_guarantee_status')
    if platform in {'成本保障已失效', 'invalid', 'conditions_not_met'} and not excluded:
        status = 'conditions_not_met'
        warnings.append('页面显示保障已失效；停止进一步修改不会自动恢复当日保障')
    elif platform in {'成本保障确认中', 'confirming'} and not excluded:
        status = 'pending_platform'
    elif platform in {'成本保障生效中', 'active', '成本保障已到账', 'paid'}:
        warnings.append('页面保障状态仅作为观测保存，不据此确认本次修改后的保障资格或赔付到账')

    name = action.get('action', '')
    is_task = bool(action.get('task_id')) or action.get('target_kind') == 'control_task'
    matching, seen = [], set()
    for row in history or []:
        if not isinstance(row, dict) or not _same_entity(row, action):
            continue
        if row.get('state') not in _SUBMITTED | {'unknown', 'submitting'}:
            continue
        at = next((_time(row.get(k)) for k in ('occurred_at', 'submitted_at', 'claimed_at', 'at')
                   if _time(row.get(k)) is not None), None)
        if at is None:
            warnings.append('同一对象的已提交记录缺少明确操作时间，频次核验不完整')
            continue
        key = row.get('id') or (row.get('action'), at.isoformat(), str(row.get('before')), str(row.get('after')))
        if key in seen:
            continue
        seen.add(key)
        if at > clock:
            check('operation_clock', 'fail', '同一对象的操作时间晚于当前检查时间，请先核对时钟', 'local_integrity_guard')
            continue
        matching.append((row, at))

    # User policy: one hour between MAIN PLAN changes. This is not a global
    # official guarantee interval and is never imposed on control-task actions.
    if name and not is_task:
        past = [at for row, at in matching]
        explicit = _time(facts.get('last_plan_change_at'))
        if explicit is not None:
            past.append(explicit)
        if past:
            elapsed = (clock - max(past)).total_seconds()
            check('main_plan_interval', 'pass' if elapsed >= 3600 else 'fail',
                  '主计划距离已知上次调整不足1小时' if elapsed < 3600 else '已知主计划调整间隔达到1小时', 'user_policy')

    if name == 'budget_up' and not is_task:
        baseline = _number(facts.get('day_initial_budget'))
        verified = facts.get('day_initial_budget_verified') is True
        baseline_date = facts.get('day_initial_budget_date')
        after = _number(action.get('after'))
        if baseline is not None and baseline > 0 and verified and baseline_date == clock.date().isoformat() and after is not None:
            cap = baseline * Decimal('1.5')
            check('daily_budget_cap', 'pass' if after <= cap else 'fail',
                  '主计划预算不得超过已核实当天初始预算的150%' if after > cap else '未超过已核实当天初始预算的150%', 'user_policy')
        else:
            check('daily_budget_cap', 'unknown', '当天初始预算未核实，无法确认日累计上调是否超过50%；首条本机操作前值不等于当天初始值', 'user_policy')

    if name == 'control_budget_up' and is_task:
        before, after = _number(action.get('before')), _number(action.get('after'))
        if before is not None and before > 0 and after is not None:
            if facts.get('saved_rule_budget_target_verified') is True:
                check('chase_budget_step','pass','按本户已明确授权的F2目标预算执行；仍检查总额度、冷却和当前规则条件','saved_account_rule')
            else:check('chase_budget_step', 'pass' if after <= before * Decimal('1.5') else 'fail',
                  '追加追投预算单次增幅不得超过50%；保持原目标值，请重新准备调控单' if after > before * Decimal('1.5')
                  else '本次追加追投预算增幅未超过50%', 'user_policy')
        else:
            check('chase_budget_step', 'unknown', '追投当前预算为零或缺失，无法计算追加预算增幅，不推算百分比', 'user_policy')

    known_nonroi_standard = family == 'standard' and delivery == 'cost_control' and goal in _NON_ROI_GOALS
    known_search = tool == 'search_first_screen' and family == 'all_domain' and scene == 'live'
    known_operation_failure = False
    if known_nonroi_standard or known_search:
        source = 'standard_cost' if known_nonroi_standard else 'search_first_screen'
        counter_actions = [('bid', _BID)]
        if known_nonroi_standard:
            counter_actions.append(('targeting', _TARGETING))
        for counter, action_names in counter_actions:
            local_count = sum(row.get('action') in action_names and row.get('state') in _SUBMITTED
                              for row, at in matching if at.date() == clock.date())
            declared = facts.get(counter + '_changes_today')
            valid_declared = (type(declared) is int and declared >= 0
                              and facts.get('changes_date') == clock.date().isoformat())
            # Local confirmed changes are a lower bound. Do not add a declared
            # total to them, since the declared total may already include them.
            count = max(local_count, declared if valid_declared else 0)
            if count > 2:
                known_operation_failure = True
                if not excluded:
                    status = 'conditions_not_met'
                warnings.append('适用产品当日已知修改次数已超过保障条件；本次预算或止损操作不能恢复当日保障')
            if name not in action_names:
                continue
            if count >= 2:
                check('official_' + counter + '_daily', 'fail',
                      '本次将使适用产品的当日' + ('出价' if counter == 'bid' else '定向') + '修改超过2次；本程序为保护保障条件停止提交',
                      'official_condition_protection', source)
            elif valid_declared and facts.get('history_known') is True:
                check('official_' + counter + '_daily', 'pass', '按已提供的完整当日记录，本次未超过适用修改次数条件', 'official_condition_protection', source)
            else:
                check('official_' + counter + '_daily', 'unknown', '缺少完整当日外部操作记录，仅本机次数不能证明符合保障修改次数条件', 'official_condition_protection', source)
    elif family == 'standard' and goal in {'payment_roi', 'product_payment_roi', 'net_roi', 'marketing_roi'}:
        warnings.append('标准投放的ROI目标适用独立规则，未套用非ROI目标的每日2次规则')
        source_ids.add('standard_cost')

    if name in _ROI:
        if facts.get('history_known') is not True:
            warnings.append('人工及其他程序的ROI修改历史未完整核实，不能保证本次操作仍满足保价条件')
        if family == 'chengfang':
            source_ids.add('chengfang_faq')
            warnings.append('乘方出价口径是综合营销ROI；整体支付ROI考核目标不能直接替代页面出价')
        elif family == 'all_domain' and scene == 'product':
            source_ids.add('product_course')
            warnings.append('商品全域支付ROI课程示例要求修改间隔至少1小时；当前完整适用条款仍需独立核对')

    # Known failure of a qualification does not forbid loss-control advertising
    # actions. Only the prospective checks above control ``allowed``.
    qualification = facts.get('guarantee_conditions')
    if (isinstance(qualification, dict) and not excluded and not known_operation_failure
            and platform not in {'成本保障已失效', 'invalid', 'conditions_not_met', '成本保障确认中', 'confirming'}
            and (known_nonroi_standard or known_search)):
        source_ids.add('standard_cost' if known_nonroi_standard else 'search_first_screen')
        orders = _number(qualification.get('eligible_orders'))
        minimum_orders = 8 if known_search else 2 if facts.get('placement') == 'search' else 6
        verified_counts = qualification.get('platform_filtered_statistics') is True
        complete = qualification.get('statistics_finalized') is True and facts.get('history_known') is True
        if orders is not None and verified_counts and orders < minimum_orders and complete:
            status = 'conditions_not_met'
        elif (complete and verified_counts and orders is not None and orders >= minimum_orders
              and qualification.get('within_guarantee_period') is True
              and qualification.get('operation_conditions_met') is True
              and qualification.get('no_disqualifying_events') is True):
            if known_search:
                actual = _number(qualification.get('actual_roi'))
                target = _number(qualification.get('weighted_target_roi'))
                result = actual < target * Decimal('.8') if actual is not None and target is not None and target > 0 else None
            else:
                ratio = _number(qualification.get('actual_to_target_cost_ratio'))
                result = ratio >= Decimal('1.2') if ratio is not None else None
            if result is not None:
                status = 'estimated_only' if result else 'conditions_not_met'

    if status == 'unknown':
        warnings.append('未取得当前计划完整保障资格证据；允许准备人工调控不表示已满足保价条件')
    warnings.append('频繁启停、异常批量或不合理修改幅度仍可能影响保障，平台按全部相关行为判定')
    return {'version': RULE_VERSION, 'checked_at': clock.isoformat(), 'status': status,
            'status_label': _LABELS[status], 'allowed': not blockers,
            'blocking_reasons': list(dict.fromkeys(blockers)), 'warnings': list(dict.fromkeys(warnings)),
            'checks': checks, 'rule_scope': scope,
            'coverage': {'complete_current_rules_verified': False,
                         'external_history_known': facts.get('history_known') is True,
                         'payout_calculated': False},
            'eligible_for_payout': False,
            'note': '保障资格与平台核算以官方数据为准；本检查不代替逐条人工确认。',
            'sources': [dict(_SOURCE[s]) for s in _SOURCE if s in source_ids]}
