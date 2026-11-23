"""Declared plan ROI configuration and decimal-grid offline tuning.

The business assessment threshold never supplies the plan's current setting.
Configuration declarations do not certify platform permissions or write APIs.
"""
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext

from .engine import _number, _time

CONFIG_FIELDS=('plan_roi_basis','plan_roi_mode','plan_roi_step','plan_roi_min','plan_roi_max','plan_roi_config_verified',
               'plan_delivery_mode','plan_optimization_goal')


def _decimal(value):
    number=_number(value)
    return Decimal(str(value)) if number is not None and number>0 else None


def roi_adjustment(last,records,direction,restore_goal=None):
    """Use one contemporaneously observed configuration; never invent a value."""
    ctx=last['raw'].get('context',{})
    ctx=ctx if isinstance(ctx,dict) else {}
    goal=last['metrics'].get('target_roi')
    evidence={'observed_plan_goal':goal,'plan_goal_basis':ctx.get('plan_roi_basis'),
              'mode':ctx.get('plan_roi_mode'),'step':ctx.get('plan_roi_step'),
              'delivery_mode':ctx.get('plan_delivery_mode'),'optimization_goal':ctx.get('plan_optimization_goal'),
              'minimum':ctx.get('plan_roi_min'),'maximum':ctx.get('plan_roi_max'),
              'observed_at':ctx.get('plan_roi_observed_at'),'snapshot_id':last['raw'].get('id'),
              'platform_verified':False,'nominal_change_pct':.05*direction,
              'note':'输入步长与上下限是来源声明，未认证平台合同；用户考核线与计划设置分别保留。'}
    missing=[]
    old=_decimal(goal)
    if old is None:missing.append('明确且大于零的计划当前ROI设置 metrics.target_roi')
    if ctx.get('plan_roi_basis')!=last['basis'] or last['basis'] not in {'overall_payment','net','marketing'}:
        missing.append('context.plan_roi_basis须明确匹配当前考核与窗口口径')
    if ctx.get('plan_roi_mode')!='roi_target':missing.append('未明确为ROI目标优化模式，不能调整转化出价模式的ROI')
    product=last['raw'].get('promotion_type')
    if product not in {'universal','chengfang','standard','search'} or (product in {'standard','search'} and last['basis']!='overall_payment'):
        missing.append('当前推广产品与ROI优化模式的适用范围未核对')
    if last['source'].get('kind') not in {'import','demo'} or ctx.get('plan_roi_config_verified') is not True:
        missing.append('计划ROI设置、输入步长和允许范围尚未明确核对')
    at=_time(ctx.get('plan_roi_observed_at'))
    if at is None or abs((at-last['captured']).total_seconds())>1:
        missing.append('计划配置观测时间须对应当前快照采集时间（容差1秒）')
    step,lower,upper=(_decimal(ctx.get(key)) for key in ('plan_roi_step','plan_roi_min','plan_roi_max'))
    if step is None or step*Decimal(10)**6!=(step*Decimal(10)**6).to_integral_value():
        missing.append('需明确正数输入步长；本地规划最多支持六位小数')
    if lower is None or upper is None or lower>upper:
        missing.append('需明确有效的计划ROI允许上下限')
    elif old is not None and not lower<=old<=upper:
        missing.append('当前计划ROI设置超出声明范围')
    # Coincident configuration observations must not select one conflicting value.
    for other in records:
        if other is last:continue
        changed=_time(last['control'].get('last_change_at'))
        if (last['control'].get('history_known') is True and other['metrics'].get('target_roi') is not None
                and other['metrics']['target_roi']!=goal and other['captured']<last['captured']
                and (changed is None or other['captured']>changed)):
            missing.append('已观测ROI设置变化与声明的最近变更时间不一致')
        if other['captured']!=last['captured']:continue
        context=other['raw'].get('context',{})
        context=context if isinstance(context,dict) else {}
        if (other['metrics'].get('target_roi')!=goal
                or any(context.get(k)!=ctx.get(k) for k in CONFIG_FIELDS)):
            missing.append('同一采集时间的计划ROI配置存在冲突');break
    after,change=None,None
    if not missing:
        with localcontext() as precision:
            precision.prec=40
            if old/step!=(old/step).to_integral_value():
                missing.append('计划当前ROI设置与声明输入步长不一致，不能先舍入旧值')
            else:
                nominal=old*(Decimal('1.05') if direction==1 else Decimal('.95'))
                new=(nominal/step).to_integral_value(rounding=ROUND_CEILING if direction==1 else ROUND_FLOOR)*step
                if restore_goal is not None:
                    ceiling=_decimal(restore_goal)
                    evidence['restore_goal']=float(ceiling) if ceiling is not None else None
                    if direction!=1 or ceiling is None or ceiling<=old or ceiling/step!=(ceiling/step).to_integral_value():
                        missing.append('原ROI目标未知、非上调方向、不高于当前值或不在当前输入网格')
                    elif Decimal('.05')<=(ceiling-old)/old<=Decimal('.10'):
                        new=ceiling
                    else:
                        # Leave a legal 5%-10% final step instead of stranding a
                        # sub-5% remainder or overshooting the original setting.
                        low=max(old*Decimal('1.05'),ceiling/Decimal('1.10'))
                        high=min(old*Decimal('1.10'),ceiling/Decimal('1.05'))
                        new=(low/step).to_integral_value(rounding=ROUND_CEILING)*step
                        if new>high:missing.append('当前输入网格没有可逐步返回原目标的5%至10%路径，不能越界或补一个小于5%的尾步')
                fraction=(new-old)/old
                if not Decimal('.05')<=abs(fraction)<=Decimal('.10') or fraction*direction<=0:
                    missing.append('按输入步长取值后没有5%至10%范围内的单次微调值')
                elif not lower<=new<=upper:
                    missing.append('微调后的ROI目标超出声明允许范围')
                elif not missing:after,change=float(new),float(fraction)
    evidence.update(actual_change_pct=change,after=after,missing=list(dict.fromkeys(missing)))
    return {'before':float(old) if old is not None else None,'after':after,'change_pct':change,
            'blockers':list(dict.fromkeys(missing)),'evidence':evidence}


def conflicting_roi_sources(recommendation,snapshots,now,max_age):
    """A fresh, explicitly same-basis source cannot silently contradict old value."""
    expected=recommendation.get('evidence',{}).get('plan_roi_configuration',{})
    for raw in snapshots:
        ctx=raw.get('context',{});src=raw.get('source',{})
        if not isinstance(ctx,dict) or ctx.get('plan_roi_basis')!=recommendation.get('metric_basis'):continue
        at=_time(ctx.get('plan_roi_observed_at'));captured=_time(src.get('captured_at'))
        if (at is None or captured is None or abs((at-captured).total_seconds())>1
                or not -120<=(now-at).total_seconds()<=max_age):continue
        goal=_number(raw.get('metrics',{}).get('target_roi'))
        if goal is None:continue
        if goal!=recommendation.get('before'):return True
        if ctx.get('plan_roi_config_verified') is True and any(ctx.get(k)!=expected.get(v) for k,v in
                (('plan_roi_step','step'),('plan_roi_min','minimum'),('plan_roi_max','maximum'),('plan_roi_mode','mode'),
                 ('plan_delivery_mode','delivery_mode'),('plan_optimization_goal','optimization_goal'))):return True
    return False
