"""Declared item inventory and live explanation alignment, never account writes."""
from collections import defaultdict
from datetime import timedelta

from .analytics import interval, OBSERVATION_NOTES
from .engine import _record, _settings, _time, _uid, _number
from .outcomes import DERIVED_OBSERVATION_NOTES
from .schema import now_cn

ROI_NOTES={'ROI 口径未确认','ROI口径未确认'}


def _ctx(row):
    value=row['raw'].get('context',{})
    return value if isinstance(value,dict) else {}


def _issues(row):
    value=row['raw'].get('quality',{})
    if not isinstance(value,dict):return None
    issues=value.get('issues',[])
    return issues if isinstance(issues,list) and all(isinstance(v,str) for v in issues) else None


def _current(row,at,now,rules):
    allowed=OBSERVATION_NOTES|DERIVED_OBSERVATION_NOTES|ROI_NOTES|{
        '缺少页面数据更新时间，不能用采集时间代替','缺少页面数据更新时间，采集时间不能替代数据时间'}
    return (at is not None and -120<=(now-at).total_seconds()<=rules['max_age_seconds']
        and -120<=(now-row['captured']).total_seconds()<=rules['max_age_seconds']
        and at<=row['captured']+timedelta(seconds=120)
        and row['source'].get('kind') in {'import','demo'} and row['source'].get('coverage')=='report'
        and _issues(row) is not None
        and not any(v not in allowed for v in row['issues']))


def _quantity(value):
    return value if value is not None and value>=0 and value==int(value) else None


def review_product_carrying(snapshots,settings,now=None):
    now=now or now_cn();rules,accounts=_settings(settings);groups=defaultdict(list)
    for raw in snapshots if isinstance(snapshots,list) else []:
        try:r=_record(raw,accounts,now)
        except (ValueError,TypeError,OverflowError,AttributeError):r=None
        if r and raw['scope'] in {'plan','product'}:groups[r['key']].append(r)
    inventory=[];plans=[];alerts=[];recommendations=[]
    for rows in groups.values():
        last=max(rows,key=lambda r:r['captured']);raw=last['raw'];ctx=_ctx(last)
        source_key=':'.join(map(str,last['key'][3:]));pid=ctx.get('product_id')
        if raw['scope']=='product':
            at=_time(ctx.get('inventory_at'));quantity=_quantity(last['metrics'].get('available_stock'))
            verified=(ctx.get('inventory_units_verified') is True and ctx.get('inventory_scope')=='product'
                      and pid==raw['entity_id'])
            current=_current(last,at,now,rules)
            observation={'account_id':raw['account_id'],'entity_id':raw['entity_id'],'product_id':pid,
                'scene':raw.get('scene'),'scope':'product','source_key':source_key,'snapshot_id':raw.get('id'),
                'inventory_at':ctx.get('inventory_at'),'performance_data_at':last['source'].get('data_at'),
                'available_stock':quantity,'clock_current':current,'inventory_verified':verified,
                'state':'unverified','units_per_hour':None,'stock_coverage_hours':None,'sales_window':None,
                'minimum_stock_hours':None,'aweme_id':ctx.get('aweme_id'),'live_room_id':ctx.get('live_room_id'),
                'platform_verified':False,'note':'库存为可售件数、销量为支付件数；订单数不作件数。SKU单点不证明整商品无货。'}
            if current and verified and quantity is not None:
                observation['state']='stock_empty' if quantity==0 else 'stock_positive_rate_unknown'
                data=last['data']
                recent=[r for r in rows if (r['data'] or r['captured'])>=data-timedelta(hours=1,minutes=5)] if data else []
                if (data is not None and -120<=(now-data).total_seconds()<=rules['max_age_seconds'] and abs((data-at).total_seconds())<=120
                    and all(_ctx(r).get('product_sales_units_verified') is True and _ctx(r).get('product_id')==pid
                            and _ctx(r).get('product_sales_scope')=='total_product'
                            and _issues(r) is not None
                            and r['data'] is not None and _quantity(r['metrics'].get('product_units_sold')) is not None for r in recent)):
                    quantity_rows=[{**r,'raw':{**r['raw'],'quality':{'issues':[
                        issue for issue in _issues(r) if issue not in ROI_NOTES]}}} for r in recent]
                    window=interval(quantity_rows,data,['product_units_sold'],rules)
                    observation['sales_window']=window
                    if window['complete']:
                        span=(_time(window['end'])-_time(window['start'])).total_seconds()/3600
                        rate=window['delta']['product_units_sold']/span
                        observation['units_per_hour']=rate
                        if rate>0:
                            observation['stock_coverage_hours']=quantity/rate
                            threshold=_number(ctx.get('minimum_stock_hours'))
                            if threshold is not None and threshold>0 and ctx.get('stock_policy_verified') is True:
                                observation['minimum_stock_hours']=threshold
                                if quantity>0:observation['state']='coverage_below_policy' if quantity/rate<threshold else 'coverage_within_policy'
                            elif quantity>0:observation['state']='coverage_known_policy_missing'
            inventory.append(observation)
        elif any(k in ctx for k in ('promoted_product_id','product_plan_mapping_verified','explained_product_id')):
            at=_time(ctx.get('product_mapping_at'));current=_current(last,at,now,rules)
            plan_id=ctx.get('plan_id') or raw['entity_id'];promoted=ctx.get('promoted_product_id')
            mapping=(current and ctx.get('product_plan_mapping_verified') is True
                     and ctx.get('promotion_product_mode')=='single_product' and isinstance(promoted,str) and bool(promoted))
            plan={'account_id':raw['account_id'],'entity_id':raw['entity_id'],'plan_id':plan_id,'scope':'plan',
                'source_key':source_key,'snapshot_id':raw.get('id'),'scene':raw.get('scene'),
                'product_id':promoted,'mapping_at':ctx.get('product_mapping_at'),'mapping_verified':mapping,
                'inventory_state':'unmatched','inventory_at':None,'available_stock':None,'stock_coverage_hours':None,
                'alignment_state':'unverified','explained_product_id':ctx.get('explained_product_id'),
                'promoted_category_id':ctx.get('promoted_category_id'),'explained_category_id':ctx.get('explained_category_id'),
                'explanation_started_at':ctx.get('explanation_started_at'),'explanation_observed_at':ctx.get('explanation_observed_at'),
                'hold_growth':False,'reasons':[],'platform_verified':False,
                'note':'只核对来源声明的唯一投放商品与当前讲解，不识别音视频、不推断换品或自动改变品类。'}
            plans.append((last,plan))
    observations=[]
    for last,plan in plans:
        ctx=_ctx(last);at=_time(plan['mapping_at'])
        newer=[_time(p['mapping_at']) for _,p in plans if p['account_id']==plan['account_id']
               and p['plan_id']==plan['plan_id'] and p['mapping_verified'] and _time(p['mapping_at'])]
        if newer and at and at<max(newer)-timedelta(seconds=1):
            plan['mapping_verified']=False;plan['reasons'].append('同计划已有较新的商品映射声明，本来源不参与当前增量否决')
        peers=[p for r,p in plans if p['account_id']==plan['account_id'] and p['plan_id']==plan['plan_id']
               and p['mapping_verified'] and at and _time(p['mapping_at']) and abs((at-_time(p['mapping_at'])).total_seconds())<=1]
        if plan['mapping_verified'] and len({p['product_id'] for p in peers})>1:
            plan['hold_growth']=True;plan['reasons'].append('同一计划近同时刻声明了不同的唯一投放商品，先核对映射')
        if plan['mapping_verified'] and ctx.get('product_id') and ctx['product_id']!=plan['product_id']:
            plan['hold_growth']=True;plan['reasons'].append('同来源的商品ID与唯一投放商品声明矛盾，先核对映射')
        if plan['mapping_verified']:
            candidates=[i for i in inventory if i['account_id']==plan['account_id'] and i['product_id']==plan['product_id']
                and i['clock_current'] and i['scene']==plan['scene'] and abs((_time(i['inventory_at'])-at).total_seconds())<=120]
            if plan['scene']=='live':
                candidates=[i for i in candidates if ctx.get('aweme_id') and ctx.get('live_room_id')
                    and i['aweme_id']==ctx['aweme_id'] and i['live_room_id']==ctx['live_room_id']]
            if candidates:
                newest=max(_time(i['inventory_at']) for i in candidates)
                chosen=[i for i in candidates if abs((_time(i['inventory_at'])-newest).total_seconds())<=1]
                known={i['available_stock'] for i in chosen if i['inventory_verified'] and i['available_stock'] is not None}
                if len(known)>1:
                    plan['inventory_state']='conflict';plan['hold_growth']=True
                    plan['reasons'].append('同商品近同时刻的可售件数存在矛盾，不挑选有利库存')
                else:
                    i=max(chosen,key=lambda i:(i['state'] in {'stock_empty','coverage_below_policy'},
                        i['inventory_verified'] and i['available_stock'] is not None,_time(i['inventory_at'])))
                    plan.update(inventory_state=i['state'],inventory_at=i['inventory_at'],available_stock=i['available_stock'],
                        stock_coverage_hours=i['stock_coverage_hours'],inventory_source_key=i['source_key'],inventory_snapshot_id=i['snapshot_id'])
                    if i['state'] in {'stock_empty','coverage_below_policy'}:
                        plan['hold_growth']=True;plan['reasons'].append('当前唯一投放商品有来源显示可售件数为零或低于已声明库存覆盖策略，不选择有利来源绕过，先核实承接')
        observed=_time(ctx.get('explanation_observed_at'));started=_time(ctx.get('explanation_started_at'));ends=_time(ctx.get('explanation_ends_at'))
        active=(plan['mapping_verified'] and plan['scene']=='live' and ctx.get('explanation_verified') is True
                and ctx.get('explanation_active') is True and _current(last,observed,now,rules)
                and started is not None and started<=observed and started<=now
                and abs((observed-at).total_seconds())<=120 and (ends is None or now<=ends and observed<=ends)
                and (ctx.get('explanation_ends_at') is None or ends is not None)
                and ctx.get('aweme_id') and ctx.get('live_room_id') and isinstance(plan['explained_product_id'],str))
        if active:
            if plan['product_id']==plan['explained_product_id']:
                if plan['promoted_category_id'] and plan['explained_category_id'] and plan['promoted_category_id']!=plan['explained_category_id']:
                    plan['alignment_state']='context_conflict';plan['hold_growth']=True
                    plan['reasons'].append('同商品的品类声明互相矛盾，不能标记匹配通过')
                else:plan['alignment_state']='same_product'
            elif plan['promoted_category_id'] and plan['explained_category_id'] and plan['promoted_category_id']!=plan['explained_category_id']:
                plan['alignment_state']='category_mismatch';plan['hold_growth']=True
                plan['reasons'].append('当前讲解与唯一投放商品的品类声明不一致，先核对排期和商品组合')
            else:plan['alignment_state']='product_mismatch_review'
        if plan['hold_growth'] or plan['alignment_state']=='product_mismatch_review':
            priority='P1' if plan['hold_growth'] else 'P2';rule='product_carrying_risk' if plan['hold_growth'] else 'product_alignment_review'
            identity=_uid(rule,plan['account_id'],plan['plan_id'],plan['source_key'])
            alerts.append({'id':identity,'priority':priority,'account_id':plan['account_id'],'entity_id':plan['entity_id'],
                'scope':'plan','source_key':plan['source_key'],'rule':rule,'title':'商品库存或讲解对应关系需复核',
                'evidence':plan.copy(),'risk':'映射和库存均为来源声明；不自动启停或替换投放商品','rule_basis':'user_strategy'})
            recommendations.append({'id':identity+'-review','priority':priority,'account_id':plan['account_id'],
                'entity_id':plan['entity_id'],'scope':'plan','source_key':plan['source_key'],'metric_basis':last['basis'],
                'target_id':plan['plan_id'],'target_type':'product_mix_review','action':'review_product_mix',
                'action_label':'复核库存、讲解排期与投放商品','before':None,'after':None,'change_pct':None,
                'parameters':{'promoted_product_id':plan['product_id'],'explained_product_id':plan['explained_product_id']},
                'reason':'；'.join(plan['reasons']) or '当前讲解商品与投放商品不同，需要核对排期；不据此推断品类错误',
                'expected_goal':'核实可售件数、销售件数和当前讲解排期，再人工复核商品组合',
                'evidence':plan.copy(),'eligible':False,'blockers':['仅生成商品组合复核，不选择替代商品或提交品类修改'],
                'risk':'来源声明不是平台认证；改动商品组合可能影响模型和成本保障',
                'rule':'product_carrying_risk' if plan['hold_growth'] else 'product_alignment_review','rule_basis':'user_strategy','mode':'simulation_only'})
        observations.append(plan)
    for i in inventory:
        if i['state'] in {'stock_empty','coverage_below_policy'}:
            alerts.append({'id':_uid('product-inventory',i['account_id'],i['entity_id'],i['source_key']),
                'priority':'P1','account_id':i['account_id'],'entity_id':i['entity_id'],'scope':'product',
                'source_key':i['source_key'],'rule':'product_inventory_review','title':'商品可售件数或库存覆盖需复核',
                'evidence':i.copy(),'risk':'未核对计划映射时不把单商品风险推广为全账户启停','rule_basis':'user_strategy'})
    return {'inventory':inventory,'plans':observations,'alerts':alerts,'recommendations':recommendations}
