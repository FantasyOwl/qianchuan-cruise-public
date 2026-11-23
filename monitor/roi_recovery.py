"""Review a linked ROI relaxation/return episode without submitting changes."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from .engine import _record,_settings,_time,_uid,_uses_base,_number
from .events import normalize_event
from .outcomes import _financial_fields,_summarize_window
from .daily_windows import daily_window as _window, valid_daily_row, inspect_daily_ledger
from .roi_tuning import roi_adjustment
from .schema import now_cn


def _fraction(old,new):
    return (Decimal(str(new))-Decimal(str(old)))/Decimal(str(old))


def review_roi_recovery(snapshots,settings,now=None,events=None):
    now=now or now_cn();rules,accounts=_settings(settings);groups=defaultdict(list)
    for raw in snapshots if isinstance(snapshots,list) else []:
        try:row=_record(raw,accounts,now)
        except (TypeError,ValueError,OverflowError,AttributeError):row=None
        if row and raw.get('scope')=='plan':groups[row['key'][:-1]].append(row)
    all_records=[r for values in groups.values() for r in values]
    events=events if isinstance(events,list) else []
    observations=[];recommendations=[]
    for records in groups.values():
        records.sort(key=lambda r:(r['date'],r['captured'],r['data'] or r['captured']));last=records[-1]
        ctx=last['raw'].get('context',{});ctx=ctx if isinstance(ctx,dict) else {}
        root_id=ctx.get('roi_recovery_event_id')
        if not isinstance(root_id,str) or not root_id:continue
        raw=last['raw'];data=last['data'];account=accounts[raw['account_id']]
        source_key=':'.join(map(str,last['key'][3:]))
        current=(data is not None and -120<=(now-data).total_seconds()<=rules['max_age_seconds']
                 and last['date']==now.date() and not last['issues'] and last['source'].get('metric_period','day')=='day')
        item={'account_id':raw['account_id'],'entity_id':raw['entity_id'],'source_key':source_key,'snapshot_id':raw.get('id'),
            'root_event_id':root_id,'data_at':data.isoformat() if data else None,'is_current':current,
            'state':'verify_episode','original_plan_goal':None,'current_plan_goal':last['metrics'].get('target_roi'),
            'next_goal':None,'step_history':[],'missing':[],'active':True,'platform_verified':False,'executed':False,
            'note':'人工报告或合成数据与同源窗口交叉复核，不证明平台已应用或因果；回调也只用于本地模拟。'}
        observations.append(item)
        def hold(message,state='verify_episode'):
            item['missing'].append(message);item['state']=state
        try:
            roots=[e for e in events if isinstance(e,dict) and e.get('id')==root_id]
            if len(roots)!=1:hold('需要唯一且明确的ROI下调根报告');continue
            root=normalize_event(roots[0],settings,now)
            identity=(root['account_id'],root['entity_id'],root['metric_basis'],root['source_key'])
            expected=(raw['account_id'],raw['entity_id'],last['basis'],last['source'].get('series_key'))
            if identity!=expected or root['action']!='roi_target_down' or root['scope']!='plan':
                hold('根报告的账户、计划、口径、精确来源或动作不匹配');continue
            if ctx.get('plan_id',raw['entity_id'])!=raw['entity_id']:
                hold('计划报告与证据实体ID不一致，不能用别名决定回调目标');continue
            wanted='simulated' if root['source']=='simulation' else 'reported_applied'
            if root['status']!=wanted:hold('根报告未明确声明已下调，不能按建议或失败回执启动恢复');continue
            old,new=(root[s].get('target_roi') for s in ('before','after'))
            if old is None or new is None or old<=0 or new<=0 or not Decimal('-.10')<=_fraction(old,new)<=Decimal('-.05'):
                hold('根报告需要明确的5%至10%ROI下调前后值');continue
            item['original_plan_goal']=old
            chain=[root]
            linked=[e for e in events if isinstance(e,dict) and e.get('roi_recovery_root_id')==root_id]
            linked=[normalize_event(e,settings,now) for e in linked]
            linked.sort(key=lambda e:(_time(e['after_at']),_time(e['before_at'])))
            if len({e['id'] for e in chain+linked})!=len(chain+linked):hold('回调链存在重复报告ID');continue
            for event in linked:
                if ((event['account_id'],event['entity_id'],event['metric_basis'],event['source_key'])!=expected
                        or event['source']!=root['source'] or event['status']!=wanted or event['scope']!='plan'):
                    hold('回调链的账户、来源、口径或应用状态不一致');break
                a,b=(event[s].get('target_roi') for s in ('before','after'))
                if (a!=new or b is None or b<=0 or b>old or not Decimal('.05')<=_fraction(a,b)<=Decimal('.10')):
                    hold('回调链旧值、方向、5%至10%步长或原目标上限不一致');break
                if _time(event['before_at'])<_time(chain[-1]['after_at'])+timedelta(minutes=max(60,rules['cooldown_minutes'])):
                    hold('上一步尚未观察至少1小时或满足冷却，不能继续回调');break
                chain.append(event);new=b
            if item['missing']:continue
            times=[(_time(e['before_at']),_time(e['after_at'])) for e in chain]
            if any(a>b or (b-a).total_seconds()>rules['max_gap_minutes']*60 or b>now for a,b in times):
                hold('回调报告边界过长、倒置或时间在未来');continue
            start=times[0][0]-timedelta(hours=2)
            if data is None:
                hold('恢复链需要明确的数据时间和完整根报告前窗口');continue
            item['cross_day']=start.date()!=data.date()
            closed_end=times[-1][1]+timedelta(hours=1) if new==old else None
            data=min(data,closed_end) if closed_end is not None else data
            expected_kinds={'demo'} if root['source']=='simulation' else {'import','browser_dom','legacy_db'}
            if any((r['raw']['account_id'],r['raw']['entity_id'],r['basis'],r['source'].get('series_key'))==expected
                    and start-timedelta(minutes=5)<=(r['data'] or r['captured'])<=data
                    and r['source']['kind']!=last['source']['kind'] for r in all_records):
                hold('恢复期间同一来源标识切换了采集类型，不能选择一部分记录忽略冲突');continue
            if any(r['data'] is None and start-timedelta(minutes=5)<=r['captured']<=data for r in records):
                hold('恢复期间同来源有缺少数据更新时间的快照，不能忽略或用采集时间代替');continue
            relevant=[r for r in records if r['data'] is not None and start-timedelta(minutes=5)<=r['data']<=data]
            if (not relevant or any(not valid_daily_row(r) or r['source']['kind'] not in expected_kinds for r in relevant)
                    or len({(r['raw'].get('scene'),r['raw'].get('promotion_type'),r['source']['kind'],_uses_base(r)) for r in relevant})!=1):
                hold('来源质量、产品、场景、模拟类型或基础台账不一致');continue
            anchor=max(relevant,key=lambda r:(r['date'],r['data'],r['captured']))
            fields=_financial_fields(anchor)
            ledger_missing,boundaries=inspect_daily_ledger(relevant,set(fields),rules)
            item['daily_boundaries']=boundaries
            if ledger_missing:
                hold('；'.join(ledger_missing));continue
            # All unrelated same-plan reports can confound the recovery window,
            # even when they name a different report source.
            ids={e['id'] for e in chain}
            if any(sum(isinstance(e,dict) and e.get('id')==identifier for e in events)!=1 for identifier in ids):
                hold('报告ID还被其他事件重复使用，不能选择其中一份应用记录');continue
            interfering=[e for e in events if isinstance(e,dict) and e.get('id') not in ids
                and e.get('account_id')==raw['account_id'] and e.get('entity_id')==raw['entity_id']
                and e.get('status') in {'reported_applied','simulated','unknown'}
                and _time(e.get('after_at')) is not None
                and times[0][0]<=_time(e['after_at'])<=(closed_end if closed_end is not None else now+timedelta(minutes=2))]
            if interfering:hold('恢复期间存在其他同计划操作报告，先排除并发变更');continue
            if (any(r['control'].get('running') is not True for r in relevant)
                    or any(r['metrics'].get('budget') is None or r['metrics']['budget']<=0 for r in relevant)
                    or any(len({r['metrics'].get(k) for r in relevant})!=1 for k in ('budget','bid'))):
                hold('恢复期间预算、出价或开启状态变化/缺失，不能隔离ROI恢复观察');continue
            if any(any(e['before'].get(k)!=e['after'].get(k) for k in ('budget','bid','running')
                       if k in e['before'] or k in e['after']) for e in chain):
                hold('ROI报告还改变了预算、出价或状态，不能隔离恢复观察');continue
            # Verify every observed goal outside the explicit report boundary gaps.
            for row in relevant:
                goal=old;inside_gap=False
                for event,(before_at,after_at) in zip(chain,times):
                    if before_at<row['data']<after_at:inside_gap=True;break
                    if row['data']>=after_at:goal=event['after']['target_roi']
                if not inside_gap and row['metrics'].get('target_roi')!=goal:
                    hold('连续快照中的ROI设置与报告链不一致，可能存在未报告变更');break
            if item['missing']:continue
            pre=_window([r for r in relevant if r['data']<=times[0][0]],start,times[0][0],set(fields),'target_roi',old,rules)
            item['pre_stall_window']=pre
            if not pre['complete'] or pre['delta'].get(fields[2])!=0:
                hold('根报告前两小时停耗或旧ROI设置尚未完整交叉核对');continue
            for event,(_,after_at) in zip(chain,times):
                due=after_at+timedelta(hours=1)
                if data<due:
                    item['observation_due_at']=due.isoformat();hold('尚未积累本次变更后完整一小时数据','observing_after_change');break
                post=_window([r for r in relevant if r['data']>=after_at],after_at,due,set(fields),'target_roi',event['after']['target_roi'],rules)
                if not post['complete']:hold('某一步回调的首小时窗口断档、回退、缺值或旧值冲突');break
                item['step_history'].append({'event_id':event['id'],'before':event['before']['target_roi'],
                    'after':event['after']['target_roi'],'after_at':event['after_at'],'first_hour':_summarize_window(post,fields,rules)})
            if item['missing']:continue
            if closed_end is None and last['metrics'].get('target_roi')!=new:hold('当前ROI设置与最近回调报告不一致');continue
            window=_window([r for r in relevant if r['data']>=times[-1][1]],data-timedelta(hours=1),data,set(fields),'target_roi',new,rules)
            if not window['complete']:hold('当前完整一小时恢复观察不足，不能沿用历史首小时结论');continue
            post=_summarize_window(window,fields,rules);item['current_window']=post;item['current_window_end']=data.isoformat()
            target=_number(account.get('target_roi'))
            if target is None or target<=0 or account.get('roi_basis')!=last['basis'] or last['basis'] not in {'overall_payment','net','marketing'}:
                hold('用户考核目标为空或口径不匹配，不借用原计划设置','returned_needs_review' if closed_end else 'target_missing')
                if closed_end:item['active']=False
                continue
            cost=anchor['metrics'].get('target_cost')
            cost_context=anchor['raw'].get('context',{})
            if (not post['roi_sample_sufficient'] or post['roi'] is None or post['roi']<target or cost is None or cost<=0
                    or cost_context.get('target_cost_basis')!=last['basis'] or post['conversion_cost'] is None or post['conversion_cost']>cost):
                hold('观察小时跑量/订单样本、ROI或同口径成本尚未同时恢复','returned_needs_review' if closed_end else 'recovery_not_qualified')
                if closed_end:item['active']=False
                continue
            for index,event in enumerate(chain[1:]):
                before_at=_time(event['before_at']);previous=chain[index]
                observed=_window([r for r in relevant if _time(previous['after_at'])<=r['data']<=before_at],
                    before_at-timedelta(hours=1),before_at,set(fields),'target_roi',previous['after']['target_roi'],rules)
                if not observed['complete']:
                    hold('已报告的某次回调前没有完整恢复观察，不能跳过历史阶段','returned_needs_review' if closed_end else 'verify_episode');break
                sample=_summarize_window(observed,fields,rules)
                if (not sample['roi_sample_sufficient'] or sample['roi'] is None or sample['roi']<target
                        or sample['conversion_cost'] is None or sample['conversion_cost']>cost):
                    hold('已报告的某次回调前跑量/ROI/成本未达到当前考核条件，先复核该步骤','returned_needs_review' if closed_end else 'verify_episode');break
            if item['missing']:
                if closed_end:item['active']=False
                continue
            item['state']='complete' if new==old else 'ready_return_step'
            if new==old:
                item['active']=False;item['completed_at']=closed_end.isoformat();item['historical']=now>closed_end
                item['note']='报告及固定归档窗口已复核返回原设置；后续行情或其他操作继续常规监测，不重新激活这条已完成恢复链。未认证平台执行。'
                continue
            adjustment=roi_adjustment(last,records,1,restore_goal=old)
            item['missing'].extend(adjustment['blockers']);item['next_goal']=adjustment['after']
            if adjustment['after'] is None:item['state']='verify_return_path';continue
            block=list(adjustment['blockers'])
            if not current:block.append('当前来源过期、时间或统计周期未核对')
            ctl=last['control']
            if ctl.get('history_known') is not True or ctl.get('guarantee_verified') is not True:block.append('调控历史或当前保障限制未核验')
            if ctl.get('running') is not True or ctx.get('manual_reviewed') is not True or ctx.get('model_stable') is not True:
                block.append('明确在投、人工复核与模型稳定条件未满足')
            changed=_time(ctl.get('last_change_at'))
            if changed is None or abs((changed-times[-1][1]).total_seconds())>1 or (now-changed).total_seconds()<rules['cooldown_minutes']*60:
                block.append('最近变更时间与报告不符或尚未满足冷却')
            count=_number(ctl.get('changes_today'))
            today_changes=sum(_time(e['after_at']).date()==last['date'] for e in chain)
            item['reported_changes_on_current_day']=today_changes
            if count is None or count<today_changes:block.append('当日变更次数不能覆盖当日的根报告与回调链，不把昨日次数算入今日')
            if last['source'].get('coverage')!='report' or last['issues']:block.append('来源覆盖范围或数据质量未核对')
            if not _uses_base(last) and last['metrics'].get('control_spend')!=0:block.append('基础计划与追加投入范围未确认')
            item['candidate_blockers']=list(dict.fromkeys(block))
            recommendations.append({'id':_uid('roi-recovery',root_id,raw['account_id'],source_key,len(chain)),
                'priority':'P2','account_id':raw['account_id'],'entity_id':raw['entity_id'],'scope':'plan','source_key':source_key,
                'metric_basis':last['basis'],'target_type':'plan_roi','target_id':raw['entity_id'],'action':'roi_target_up',
                'action_label':'分步恢复原计划ROI目标','before':adjustment['before'],'after':adjustment['after'],'change_pct':adjustment['change_pct'],
                'parameters':{'roi_recovery_root_id':root_id,'original_plan_goal':old,'step_number':len(chain)},
                'reason':'下调报告和前两小时停耗已交叉复核，当前完整小时跑量、ROI及成本达到观察条件，复核一小步回调',
                'expected_goal':'观察回调后至少一小时的消耗、转化和成本，逐步返回原计划设置而不超过它',
                'risk':'来源和人工报告不是平台认证；ROI执行适配器与观察服务断开，恢复相关性不证明因果或利润。',
                'evidence':{'root_event_id':root_id,'strategy_target_roi':target,'plan_roi_configuration':adjustment['evidence'],
                            'current_window':post,'step_history':item['step_history'],'daily_boundaries':boundaries,
                            'reported_changes_on_current_day':today_changes},
                'blockers':list(dict.fromkeys(block)),'eligible':not block,'mode':'simulation_only','rule_basis':'user_strategy','rule':'roi_recovery_step'})
        except (ValueError,TypeError,KeyError,AttributeError,OverflowError,ZeroDivisionError):
            hold('报告、配置或来源格式无效，未据此推进恢复')
    return {'observations':observations,'recommendations':recommendations}
