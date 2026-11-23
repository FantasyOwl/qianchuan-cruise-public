"""Ordered decline maintenance from exact reports and sampled inventories.

No account operations. A manual report and declared inventory are evidence to
cross-check locally, never platform certification or permission to execute.
"""
from collections import defaultdict
from datetime import timedelta
import re

from .analytics import interval
from .engine import _amount_fields, _number, _record, _settings, _time, _uid, _uses_base
from .events import normalize_event
from .schema import now_cn


def _ctx(row):
    value=row['raw'].get('context',{})
    return value if isinstance(value,dict) else {}


def _inventory(row):
    ctx=_ctx(row);items=ctx.get('creative_ids');at=_time(ctx.get('creative_inventory_at'))
    if (ctx.get('creative_inventory_verified') is not True or not isinstance(items,list) or len(items)>1000
            or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',x) for x in items)
            or len(set(items))!=len(items) or not at or not row['data'] or abs((at-row['data']).total_seconds())>1):return None
    return set(items)


def review_maintenance(snapshots,settings,now=None,events=None):
    now=now or now_cn();rules,accounts=_settings(settings);groups=defaultdict(list);observations=[];recommendations=[]
    for raw in snapshots if isinstance(snapshots,list) else []:
        try:row=_record(raw,accounts,now)
        except (ValueError,TypeError,AttributeError,OverflowError):row=None
        if row and raw['scope']=='plan':groups[row['key']].append(row)
    for key,rows in groups.items():
        rows.sort(key=lambda r:(r['data'] or r['captured'],r['captured']));last=rows[-1];raw=last['raw'];ctx=_ctx(last);src=last['source'];dt=last['data']
        if last['control'].get('lifecycle')!='declining':continue
        source_key=':'.join(map(str,key[3:]));missing=[]
        item={'account_id':key[0],'entity_id':key[1],'scope':'plan','source_key':source_key,'series_key':src.get('series_key'),
              'snapshot_id':raw.get('id'),'data_at':src.get('data_at'),'state':'verify_decline','is_current':False,
              'declared_lifecycle':'declining','is_primary_plan':ctx.get('is_primary_plan'),'missing':missing,
              'refresh_event_id':ctx.get('creative_refresh_event_id'),'post_refresh_windows':[],
              'order':['refresh_creatives','review_bid_maintenance','review_budget_handover'],
              'executed':False,'execution_eligible':False,'platform_verified':False,
              'note':'先补素材并观察，再复核微调出价方案，最后明确选择接收计划进行预算交接；人工报告与本地观察不证明因果或平台执行。'}
        observations.append(item)
        fresh=bool(dt and not last['issues'] and last['date']==now.date() and src.get('metric_period','day')=='day'
                   and -120<=(now-dt).total_seconds()<=rules['max_age_seconds'])
        item['is_current']=fresh
        if not fresh:missing.append('需要新鲜、同日且质量完整的日级来源')
        if src.get('kind') not in {'import','demo'} or src.get('coverage')!='report':missing.append('需要可核对完整素材清单的独立报告')
        if _inventory(last) is None:missing.append('当前素材清单缺失、未核验或时间不对应')
        if type(ctx.get('is_primary_plan')) is not bool:missing.append('需要明确主力或非主力角色，不能按名称或预算猜测')
        if last['control'].get('running') is not True or last['control'].get('history_known') is not True:
            missing.append('计划开启与完整变更历史尚未声明核验')
        changed=_time(last['control'].get('last_change_at'))
        if (changed and dt and changed>dt) or (changed is None and _number(last['control'].get('changes_today'))!=0):
            missing.append('最近变更时间未知或晚于数据时间')
        episode=_time(ctx.get('decline_started_at'));item['decline_started_at']=episode.isoformat() if episode else None
        if not episode or not dt or episode>dt or episode.date()!=last['date']:
            missing.append('需要明确且位于当前统计日内的衰退起点')
        if missing:continue
        event_id=ctx.get('creative_refresh_event_id')
        if not event_id:
            unlinked=[e for e in events or [] if isinstance(e,dict) and e.get('account_id')==key[0] and e.get('entity_id')==key[1]
                      and e.get('action')=='refresh_creatives' and _time(e.get('after_at')) and episode<=_time(e['after_at'])<=dt]
            if unlinked:
                item['state']='verify_refresh_report';missing.append('已有本轮素材报告但未明确关联事件ID，不重复建议补充');continue
            item['state']='refresh_required'
        else:
            item['state']='verify_refresh_report'
            candidates=[e for e in events or [] if isinstance(e,dict) and e.get('id')==event_id]
            if len(candidates)!=1:missing.append('素材补充报告缺失或同ID重复');continue
            try:event=normalize_event(candidates[0],settings,now)
            except (ValueError,TypeError,AttributeError,OverflowError):missing.append('素材补充报告格式无效');continue
            expected=('simulation','simulated') if src['kind']=='demo' else ('manual_verified_report','reported_applied')
            if (event['account_id']!=key[0] or event['entity_id']!=key[1] or event['scope']!='plan'
                    or event['source_key']!=src.get('series_key') or event['metric_basis']!=last['basis']
                    or event['action']!='refresh_creatives' or (event['source'],event['status'])!=expected):
                missing.append('报告的账户、计划、来源、口径、动作或应用声明不匹配');continue
            before,after=_time(event['before_at']),_time(event['after_at'])
            if before<episode or after>dt or (after-before).total_seconds()>rules['max_gap_minutes']*60:
                missing.append('补充时间早于本轮衰退、晚于数据或变更边界过宽');continue
            old,new=(set(event[s]['material_ids']) for s in ('before','after'))
            boundaries=[]
            for boundary,inventory in ((before,old),(after,new)):
                selected=[r for r in rows if r['data'] and abs((r['data']-boundary).total_seconds())<=300]
                # A conflicting near-boundary inventory cannot be resolved by choosing a later capture.
                exact=[r for r in selected if r['data']==boundary]
                selected=exact or selected
                if not selected or any(r['issues'] or _inventory(r)!=inventory for r in selected):
                    missing.append('补充报告前后素材集合没有与同源完整清单相符的边界');break
                boundaries.append(min(selected,key=lambda r:abs((r['data']-boundary).total_seconds())))
            if missing:continue
            if (any(boundaries[0]['metrics'].get(k)!=boundaries[1]['metrics'].get(k) for k in ('budget','bid','target_roi'))
                    or any(r['control'].get('running') is not True for r in boundaries)):
                missing.append('补充素材前后边界同时存在其他数值或启停变化，不能单独复核素材阶段');continue
            end=after+timedelta(hours=2)
            item.update(refresh_verified_locally=True,added_material_count=len(new-old),post_observation_end=end.isoformat())
            if dt<end:
                item['state']='observing_refresh';missing.append('补充后尚未积累完整两小时观察');continue
            selected=[r for r in rows if r['data'] and after<=r['data']<=end]
            if not selected or any(_inventory(r)!=new for r in selected):
                missing.append('观察期素材集合变化或清单核验缺失');continue
            if any(any(r['metrics'].get(k)!=boundaries[-1]['metrics'].get(k) for k in ('budget','bid','target_roi'))
                   or r['control'].get('running') is not True or r['control'].get('last_change_at')!=boundaries[-1]['control'].get('last_change_at') for r in selected):
                missing.append('补充后观察期同时存在预算、出价、ROI目标或启停变化，不能单独复核素材阶段');continue
            other=[e for e in events or [] if isinstance(e,dict) and e.get('id')!=event_id and e.get('account_id')==key[0]
                   and e.get('entity_id')==key[1] and _time(e.get('after_at')) and after<_time(e['after_at'])<=end
                   and e.get('status') not in {'failed','rejected'}]
            if other:missing.append('补充后观察期存在其他已报告或结果未知的变更');continue
            cost,revenue=_amount_fields(last);order='base_orders' if _uses_base(last) else 'orders'
            if not revenue or not cost or (last['metrics'].get('control_spend') is None and not _uses_base(last)):
                missing.append('缺少明确基础台账和追加投入范围');continue
            for offset in (1,2):
                window=interval(rows,after+timedelta(hours=offset),[cost,revenue,order],rules)
                if window['complete']:
                    d=window['delta'];window.update(spend=d[cost],orders=d[order],roi=d[revenue]/d[cost] if d[cost]>0 else None,
                                                    ledger='base' if _uses_base(last) else 'overall')
                item['post_refresh_windows'].append(window)
            windows=item['post_refresh_windows']
            if not all(w['complete'] and w.get('spend',0)>=rules['min_spend'] and w.get('roi') is not None for w in windows):
                missing.append('补充后的两小时台账不完整、发生重置/断档或消耗样本不足');continue
            target=_number(accounts[key[0]].get('target_roi'))
            if target is None or target<=0 or accounts[key[0]].get('roi_basis')!=last['basis'] or last['basis'] not in {'overall_payment','net','marketing'}:
                item['state']='target_missing';missing.append('用户ROI目标为空或口径不相容，不能判恢复或失败');continue
            item['strategy_target_roi']=target
            if all(w['roi']>=target and w['orders']>=rules['min_orders'] for w in windows):
                item['state']='recovered_observation';continue
            if not all(w['roi']<target*.8 for w in windows):
                item['state']='inconclusive';missing.append('恢复与失败条件均未明确满足，不能推进交接');continue
            item['state']='bid_review_required'
            if ctx.get('creative_replacement_failed') is not True:
                missing.append('补充后低ROI已观察，仍需人工核对素材与承接原因');continue
            review_at=_time(ctx.get('bid_maintenance_review_at'))
            item['bid_review_at']=review_at.isoformat() if review_at else None
            if ctx.get('bid_maintenance_reviewed') is True and review_at and end<=review_at<=dt:
                item['state']='handover_ready'
            else:missing.append('出价维护方案需在补充素材观察完成后明确复核；不假定已经调价')
        if item['state'] in {'refresh_required','bid_review_required','handover_ready'}:
            action={'refresh_required':'refresh_creatives','bid_review_required':'review_bid_maintenance','handover_ready':'review_budget_handover'}[item['state']]
            label={'refresh_required':'先补充新素材并记录完整清单','bid_review_required':'再复核微调出价方案','handover_ready':'最后明确选择新计划复核预算交接'}[item['state']]
            recommendations.append({'id':_uid(*key,'decline_maintenance',action),'priority':'P1','account_id':key[0],'entity_id':key[1],
                'scope':'plan','source_key':source_key,'metric_basis':last['basis'],'target_type':'plan_creatives' if action=='refresh_creatives' else 'plan',
                'target_id':key[1],'action':action,'action_label':label,'before':None,'after':None,'change_pct':None,
                'parameters':{'observe_hours':2,'ordered_step':item['order'].index(action)+1},'reason':item['note'],
                'expected_goal':'按顺序核验维护效果，避免直接关停主力或跳过素材阶段交接预算',
                'risk':'两小时观察是本地复核条件；没有证明因果、平台执行、保障资格或调价已发生。',
                'evidence':{'state':item['state'],'refresh_event_id':item['refresh_event_id'],'post_refresh_windows':item['post_refresh_windows']},
                'eligible':False,'blockers':['此为维护顺序复核，具体变更须另过当前产品、历史和幅度条件'],
                'mode':'simulation_only','rule':'decline_maintenance','rule_basis':'user_strategy'})
    return {'observations':observations,'recommendations':recommendations,'executed':False,'execution_eligible':False}
