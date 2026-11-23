"""Phase-specific local review; declarations never become platform certification."""
import math
from decimal import Decimal
from datetime import datetime,timezone,timedelta

CN=timezone(timedelta(hours=8))


def _number(value):
    if type(value) not in (int,float):return None
    if value<0 or value>1e15 or not math.isfinite(value):return None
    return float(value)


def _time(value):
    if not isinstance(value,str) or len(value)>50:return None
    try:
        at=datetime.fromisoformat(value.replace('Z','+00:00'))
        return at.replace(tzinfo=CN) if at.tzinfo is None else at.astimezone(CN)
    except (TypeError,ValueError,OverflowError):return None


def inspect_lifecycle(last,records,windows,rules,target,now,base=False,min_impressions=1000):
    raw=last['raw'];ctx=raw.get('context',{});ctx=ctx if isinstance(ctx,dict) else {}
    m=last['metrics'];src=last['source'];declared=last['control'].get('lifecycle','unknown')
    declared=declared if isinstance(declared,str) and declared in {'cold','growing','mature','declining'} else 'unknown'
    created=_time(ctx.get('created_at'));data=last['data']
    age=(data-created).total_seconds()/3600 if created is not None and data is not None else None
    current=(data is not None and -120<=(now-data).total_seconds()<=rules['max_age_seconds']
             and last['date']==now.date() and src.get('metric_period','day')=='day' and not last['issues'])
    phase='unverified';conflicts=[];missing=[]
    if age is not None and age<0:conflicts.append('计划创建时间晚于业绩数据时间')
    if declared=='cold' and age is not None and age>72:conflicts.append('声明冷启动但计划年龄已超过0至3天范围')
    if declared in {'growing','mature'} and age is not None and 0<=age<=72:
        conflicts.append('创建时间仍在0至3天冷启动范围内，与成长/成熟声明冲突')
    for row in records:
        other=row['raw'].get('context',{});other=other if isinstance(other,dict) else {}
        other_created=_time(other.get('created_at'))
        if created is not None and other_created is not None and created!=other_created:
            conflicts.append('同来源计划创建时间不一致');break
        if (row['captured']==last['captured'] and
                (row['control'].get('lifecycle','unknown')!=declared or other.get('content_format')!=ctx.get('content_format'))):
            conflicts.append('同一采集时间存在生命周期或内容体裁冲突');break
    if conflicts:phase='stage_conflict'
    elif declared=='cold' and age is not None and 0<=age<=72:phase='cold_observing'
    elif declared in {'growing','mature','declining'}:phase=declared
    elif declared=='cold':missing.append('明确创建时间和独立数据更新时间以核对冷启动范围')
    else:missing.append('明确生命周期声明；趋势候选不能替代确认')
    spend=m.get('base_spend' if base else 'spend');budget=m.get('budget')
    threshold=_number(ctx.get('cold_min_test_spend'))
    cold=phase=='cold_observing'
    cold_missing=[]
    if cold:
        if raw.get('scene')!='product' or ctx.get('content_format') not in {'image_text','short_video'}:
            cold_missing.append('明确图文/短视频带货计划；不把此预算分支套用到直播场景')
        if not current:cold_missing.append('当前同日、日累计且数据质量完整的来源')
        if src.get('kind') not in {'import','demo'} or src.get('coverage')!='report':
            cold_missing.append('完整范围的导入/模拟报告；可见页面不能认证测试覆盖')
        if ctx.get('manual_reviewed') is not True:cold_missing.append('计划场景与操作对象尚未人工复核')
        if ctx.get('plan_budget_period')!='day' or ctx.get('plan_budget_period_verified') is not True or ctx.get('budget_mode')!='specified':
            cold_missing.append('明确已核对的指定日预算，不混用总预算或建议预算')
        if budget is None or budget<300:cold_missing.append('当前指定日预算未达到用户300元起步值；不能自动跳涨补齐')
        if threshold is None or threshold<=0 or ctx.get('cold_test_threshold_verified') is not True:
            cold_missing.append('明确已核对的当日冷启动测试消耗门槛，未设置默认门槛')
        elif spend is None or spend<threshold:cold_missing.append('同口径当日测试消耗尚未达到门槛')
        if target is None:cold_missing.append('用户考核目标为空或ROI口径不符')
        target_cost=m.get('target_cost')
        if target_cost is None or target_cost<=0 or ctx.get('target_cost_basis')!=last['basis']:
            cold_missing.append('与订单和ROI台账一致的明确目标成本口径')
        if ctx.get('model_stable') is not True:cold_missing.append('模型稳定性尚未明确确认')
        if ctx.get('compliance_ok') is not True:cold_missing.append('当前素材/计划合规状态尚未确认')
        if len(windows)!=3 or not all(w.get('complete') and w.get('spend') is not None and w['spend']>=rules['min_spend']
                and w.get('orders') is not None and w['orders']>=rules['min_orders'] and w.get('roi') is not None
                and target is not None and w['roi']>=target and w.get('conversion_cost') is not None
                and target_cost is not None and w['conversion_cost']<=target_cost for w in windows):
            cold_missing.append('三个完整小时的消耗、订单、ROI与成本均达标样本')
        if created is not None and any(_time(w.get('start')) is not None and _time(w['start'])<created for w in windows):
            cold_missing.append('测试窗口不能早于计划创建时间')
        prefix='base_' if base else ''
        clicks,impressions=m.get(prefix+'clicks'),m.get(prefix+'impressions')
        ctr=None if clicks is None or impressions is None or impressions<=0 else clicks/impressions*100
        industry=m.get('industry_ctr');baseline_at=_time(ctx.get('industry_baseline_at'))
        baseline_source=ctx.get('industry_baseline_source')
        if (industry is None or not 0<=industry<=100 or ctx.get('industry_baseline_verified') is not True
                or not isinstance(baseline_source,str) or not baseline_source.strip()
                or not isinstance(ctx.get('industry_id'),str) or not ctx['industry_id']
                or ctx.get('industry_id')!=ctx.get('baseline_industry_id') or baseline_at is None
                or not 0<=(now-baseline_at).total_seconds()<=30*86400):
            cold_missing.append('同业分类、30天内且明确来源的已核对行业CTR基线')
        elif (clicks is None or impressions is None or impressions<min_impressions or clicks>impressions
                or clicks!=int(clicks) or impressions!=int(impressions)):
            cold_missing.append('达到本地展示样本门槛的同台账点击与展示计数')
        elif ctr<industry:cold_missing.append('冷启动CTR低于已核对行业基线，先复核素材')
        if ctx.get('content_format')=='short_video' and m.get('completion_3s') is None:
            cold_missing.append('短视频缺少3秒完播率观察值；不对图文虚构视频完播率')
        missing.extend(cold_missing)
        if not cold_missing:phase='cold_ready'
    focus={'cold_observing':'先核对CTR/短视频3秒完播率与测试门槛，等待正向样本',
           'cold_ready':'测试消耗和质量达标后小步加预算，继续核对通用限额与冷却',
           'growing':'三个完整小时ROI与消耗稳定后小步放量',
           'mature':'模型稳定时微调出价，至少两小时后复核降价效果',
           'declining':'先补素材观察，再复核出价方案，最后复核预算交接',
           'stage_conflict':'先消除年龄、声明或来源冲突，阻断增量与成熟期压价',
           'unverified':'补齐明确阶段；观察候选不授予策略资格'}[phase]
    plan_id=ctx.get('plan_id')
    bid_review={'state':'not_cold_branch','target_cost':None,'observed_bid':m.get('bid'),'ceiling':None,
                'target_ratio':rules.get('cold_bid_max_target_ratio'),
                'note':'用户确认最多高40%，上限=同口径目标成本×1.4。上限不是建议新出价，不用于ROI出价或未知推广类型。'}
    if declared=='cold':
        if rules.get('cold_bid_max_target_ratio')!=1.4:bid_review['state']='interpretation_pending'
        elif phase not in {'cold_observing','cold_ready'} or not current:bid_review['state']='stage_or_time_unverified'
        elif raw.get('promotion_type')!='standard' or ctx.get('plan_bid_mode')!='conversion_cost':bid_review['state']='not_standard_cost_bid'
        elif m.get('target_cost') is None or m['target_cost']<=0 or ctx.get('target_cost_basis')!=last['basis']:
            bid_review['state']='target_cost_unverified'
        else:
            ceiling=float(Decimal(str(m['target_cost']))*Decimal('1.4'))
            bid_review.update(target_cost=m['target_cost'],ceiling=ceiling,
                state='bid_missing' if m.get('bid') is None else 'above_user_cap' if Decimal(str(m['bid']))>Decimal(str(m['target_cost']))*Decimal('1.4') else 'within_user_cap')
    return {'account_id':raw['account_id'],'entity_id':raw['entity_id'],'plan_id':plan_id if isinstance(plan_id,str) and plan_id else raw['entity_id'],
        'source_key':':'.join(map(str,last['key'][3:])),'snapshot_id':raw.get('id'),'data_at':data.isoformat() if data else None,
        'is_current':bool(current),'declared_stage':declared,'state':phase,'age_hours':age,'created_at':created.isoformat() if created else None,
        'focus':focus,'conflicts':list(dict.fromkeys(conflicts)),'missing':list(dict.fromkeys(missing)),
        'cold_budget_ready':phase=='cold_ready','daily_test_spend':spend,'cold_min_test_spend':threshold,'budget':budget,
        'initial_daily_budget_policy':300,'completion_3s':m.get('completion_3s'),
        'initial_budget_review':cold and current and raw.get('scene')=='product' and ctx.get('plan_budget_period')=='day'
            and ctx.get('budget_mode')=='specified' and budget is not None and budget<300,
        'bid_cap_status':('user_confirmed_1_4' if rules.get('cold_bid_max_target_ratio')==1.4 else 'user_interpretation_pending') if declared=='cold' else 'not_cold_branch',
        'cold_bid_review':bid_review,
        'platform_verified':False,'execution_eligible':False,
        'note':'0至3天和300元起步为用户策略；三小时及样本条件为本地观察保护。阶段声明不由本模块改写，预算就绪也须经过通用风险、历史、限额与冷却检查。'}


def lifecycle_blockers(action,account_id,plan_id,reviews):
    current=[r for r in reviews if r['account_id']==account_id and r['plan_id']==plan_id and r['is_current']]
    active={'budget_up','control_budget_up','create_chase','one_click_boost','roi_target_down','bid_down'}
    if action not in active:return []
    if any(r.get('cold_bid_review',{}).get('state')=='above_user_cap' for r in current):
        return ['冷启动已观察成本出价超过用户确认的目标成本1.4倍上限，先核对出价，不追加放量或自动改价']
    if any(r['state']=='stage_conflict' for r in current):return ['同计划当前生命周期或创建时间存在冲突，先核对阶段']
    declared={r['declared_stage'] for r in current if r['declared_stage']!='unknown'}
    if len(declared)>1:return ['同计划其他新鲜来源的生命周期声明冲突，不能选择有利阶段调控']
    if action in {'create_chase','bid_down'} and any(r['state'] in {'cold_observing','cold_ready'} for r in current):
        return ['计划仍在已确认冷启动阶段，不能追投或套用成熟期压价']
    return []
