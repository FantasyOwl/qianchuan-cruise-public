"""Synthetic series stay outside the production snapshot database."""
from datetime import timedelta
from .schema import now_cn


def scenario(settings, name="low_roi"):
    # All samples are synthetic. Fixed simulation time avoids crossing midnight.
    now = now_cn().replace(hour=16, minute=0, second=0, microsecond=0)
    account = settings["accounts"][0]
    snapshots = []
    for i in range(25):
        at = now - timedelta(minutes=240 - i * 10)
        spend = 100 + i * 30
        roi = 1.1 if name == "low_roi" else 4.0
        snapshots.append({"id": f"demo-{name}-{i}", "account_id": account["id"], "entity_id": "demo-plan",
            "scope": "plan", "scene": "live", "promotion_type": "universal", "metric_basis": "overall_payment",
            "source": {"kind": "demo", "url": "", "captured_at": at.isoformat(), "data_at": at.isoformat(),
                       "metric_date": at.date().isoformat(), "series_key": "demo-plan", "coverage": "report"},
            "metrics": {"spend": spend, "gmv": spend * roi, "roi": roi, "budget": 2000, "control_spend": 0,
                        "orders": 5 + i * 3, "impressions": 1000 + i * 300, "clicks": 50 + i * 15},
            "quality": {"issues": []}, "control": {"history_known": True, "guarantee_verified": True, "last_change_at": (now-timedelta(hours=6)).isoformat(),
                "budget_original": 2000, "budget_increase_today": 0, "changes_today": 0, "running": True, "lifecycle": "mature"}})
    import copy
    cfg = copy.deepcopy(settings)
    cfg["accounts"][0]["target_roi"] = 2.5
    cfg["accounts"][0]["roi_basis"] = "overall_payment"
    return snapshots, cfg, now


def control_creation_scenario(settings):
    """Fictitious live plan: three active hours followed by one stalled hour."""
    import copy
    cfg=copy.deepcopy(settings)
    cfg['accounts']=[{'id':'9990001','name':'合成直播户','scene':'live','roi_basis':'overall_payment','target_roi':2,'observe':True}]
    now=now_cn().replace(hour=16,minute=0,second=0,microsecond=0);rows=[]
    for n in range(25):
        at=now-timedelta(minutes=(24-n)*10);step=min(n,18)
        rows.append({'id':'demo-create-'+str(n),'account_id':'9990001','entity_id':'7333333333333333','scope':'plan',
            'scene':'live','promotion_type':'universal','metric_basis':'overall_payment',
            'source':{'kind':'demo','series_key':'demo-boost-parent','metric_period':'day','metric_date':at.date().isoformat(),
                'captured_at':at.isoformat(),'data_at':at.isoformat(),'coverage':'report'},
            'metrics':{'spend':step*100,'gmv':step*250,'net_gmv':step*250,'comprehensive_cost':step*100,
                'orders':step*2,'clicks':step*40,'impressions':step*1000,'budget':10000,'control_spend':0},
            'quality':{'issues':[]},'control':{'running':True,'lifecycle':'mature','history_known':True,
                'guarantee_verified':True,'changes_today':0,'last_change_at':None},
            'context':{'plan_id':'7333333333333333','account_source_key':'demo-boost-account','account_metrics_verified':True,
                'model_stable':True,'compliance_ok':True,'anchor_ready':True,'carrying_ready':True,'promotion_peak':False,
                'manual_reviewed':True,'is_primary_plan':False,'created_at':(now-timedelta(days=4)).isoformat(),
                'plan_delivery_mode':'cost_control','plan_multi_aweme':False,'plan_roi_config_verified':True,
                'plan_roi_observed_at':at.isoformat(),'plan_optimization_goal':'AD_CONVERT_TYPE_LIVE_PAY_ROI'}})
    account=copy.deepcopy(rows[-1]);account.update(id='demo-create-account',entity_id='9990001',scope='account',context={})
    account['source']['series_key']='demo-boost-account'
    account['metrics'].update(spend=10000,gmv=25000,net_gmv=25000,comprehensive_cost=10000,ctr=3,cvr=3,control_share_pct=10)
    rows.append(account)
    return rows,cfg,now


def creative_rotation_scenario(settings):
    """Fictitious account, declared registry and a three-hour weak video."""
    import copy
    cfg=copy.deepcopy(settings)
    cfg['accounts']=[{'id':'9990001','name':'合成商品户','scene':'product','roi_basis':'overall_payment','target_roi':2,'observe':True}]
    now=now_cn().replace(hour=16,minute=0,second=0,microsecond=0);rows=[]
    values={'spend':[600,500,400],'gmv':[1500,1000,600],'orders':[72,48,24],
            'clicks':[600,360,180],'impressions':[12000,9000,6000]}
    total={k:0 for k in values}
    for n in range(19):
        at=now-timedelta(minutes=(18-n)*10)
        if n:
            for key in total:total[key]+=values[key][(n-1)//6]/6
        rows.append({'id':'demo-rotation-'+str(n),'account_id':'9990001','entity_id':'1001','scope':'creative',
            'scene':'product','promotion_type':'universal','metric_basis':'overall_payment',
            'source':{'kind':'demo','series_key':'demo-rotation-weak','metric_period':'day','metric_date':at.date().isoformat(),
                'captured_at':at.isoformat(),'data_at':at.isoformat(),'coverage':'report'},
            'metrics':{**total,'net_gmv':total['gmv'],'comprehensive_cost':total['spend'],'control_spend':0,'budget':1000},
            'quality':{'issues':[]},'control':{'running':True,'lifecycle':'mature','history_known':True,'guarantee_verified':True,
                'changes_today':0,'last_change_at':None},
            'context':{'creative_type':'programmatic','parent_plan_id':'2001','plan_id':'2001','manual_reviewed':True,
                'created_at':(now-timedelta(days=4)).isoformat(),'is_primary_plan':False}})
    parent=copy.deepcopy(rows[-1]);parent.update(id='demo-rotation-parent',entity_id='2001',scope='plan')
    parent['source']['series_key']='demo-rotation-parent';rows.append(parent)
    registry={'format':'qianchuan_creative_rotation_registry_v1',
        'duplicate_inventory':[{'account_id':'9990001','observed_at':now.isoformat(),'source':'合成账户分组计数',
            'verified':True,'complete':True,'groups':[{'group_id':'demo-old-group','active_count':1},{'group_id':'demo-new-group','active_count':0}]}],
        'plans':[{
        'account_id':'9990001','plan_id':'2001','parent_snapshot_id':parent['id'],'observed_at':now.isoformat(),
        'inventory_verified':True,'inventory_complete':True,'inventory_source':'合成完整配置',
        'duplicate_groups_verified':True,'multi_aweme':False,
        'capacity':{'unit':'configured_entries','active_count':1,'maximum':3,'verified':True,'source':'合成容量声明'},
        'slots':[{'snapshot_id':'demo-rotation-18','material_type':'VIDEO','material_id':'7333333333333333',
            'native':{'video_id':'synthetic-old-video','image_mode':'VIDEO_VERTICAL'},'product_id':'3001',
            'duplicate_group_id':'demo-old-group','mapping_verified':True,'delete_class':'SELF_PROVIDED','deletion_eligibility_verified':True}],
        'pool':[{'id':'demo-candidate-new','material_type':'VIDEO','native':{'video_id':'synthetic-new-video','image_mode':'VIDEO_VERTICAL'},
            'product_id':'3001','duplicate_group_id':'demo-new-group','enabled':True,'reviewed':True,
            'reviewed_at':now.isoformat(),'queued_at':(now-timedelta(days=1)).isoformat(),'review_source':'合成候选审核'}]}]}
    return rows,cfg,now,registry


def creative_idle_scenario(settings):
    """Prior activity lies outside the current 24h; each day has its own ledger."""
    import copy
    seeds,cfg,now=scenario(settings,'creative_idle')
    cfg['accounts'][0]['target_roi']=None
    rows=[]
    for n in range(145):
        at=now-timedelta(minutes=10*(144-n));row=copy.deepcopy(seeds[-1])
        row.update(id='demo-idle-'+str(n),scope='creative',entity_id='demo-idle-creative')
        row['source'].update(captured_at=at.isoformat(),data_at=at.isoformat(),metric_date=at.date().isoformat(),
            metric_period='day',series_key='demo-creative-daily-ledger')
        row['metrics']={'spend':100 if at.date()<now.date() else 0,'clicks':5 if at.date()<now.date() else 0}
        row['context']={'parent_plan_id':'demo-parent-plan','plan_id':'demo-parent-plan','creative_type':'custom',
            'created_at':(now-timedelta(days=4)).isoformat(),'manual_reviewed':True}
        rows.append(row)
    opening=next(r for r in rows if r['source']['data_at']==now.replace(hour=0).isoformat())
    opening['context'].update(day_boundary_role='opening',day_boundary_verified=True)
    closing=copy.deepcopy(opening);closing['id']='demo-idle-explicit-close'
    closing['source']['metric_date']=(now-timedelta(days=1)).date().isoformat()
    closing['context'].update(day_boundary_role='closing',day_boundary_verified=True)
    closing['metrics']={'spend':100,'clicks':5};rows.append(closing)
    return rows,cfg,now


def product_carrying_scenario(settings):
    """Quantity-based inventory and declared live alignment, isolated from real data."""
    import copy
    plans,cfg,now=scenario(settings,'stable_growth');other=copy.deepcopy(plans);products=copy.deepcopy(plans)
    for row in plans:
        row['context']={'plan_id':row['entity_id'],'promoted_product_id':'demo-product-one',
            'product_plan_mapping_verified':True,'promotion_product_mode':'single_product',
            'product_mapping_at':row['source']['captured_at'],'aweme_id':'demo-aweme','live_room_id':'demo-room',
            'explanation_verified':True,'explanation_active':True,'explained_product_id':'demo-product-two',
            'promoted_category_id':'demo-category-one','explained_category_id':'demo-category-two',
            'explanation_observed_at':row['source']['captured_at'],
            'explanation_started_at':(now-timedelta(hours=4)).isoformat()}
    for row in other:
        row.update(id='peer-'+row['id'],entity_id='demo-unrelated-plan');row['source']['series_key']='demo-unrelated-plan';row['context']={}
    for n,row in enumerate(products):
        row.update(id='product-'+row['id'],scope='product',entity_id='demo-product-one');row['source']['series_key']='demo-product-quantity-report'
        row['metrics'].update(available_stock=12,product_units_sold=n*4,product_orders=n*2)
        row['context']={'product_id':'demo-product-one','inventory_scope':'product','inventory_units_verified':True,
            'inventory_at':row['source']['captured_at'],'product_sales_units_verified':True,'product_sales_scope':'total_product',
            'minimum_stock_hours':1,'stock_policy_verified':True,'aweme_id':'demo-aweme','live_room_id':'demo-room'}
    return plans+other+products,cfg,now


def live_exclusion_scenario(settings):
    """Two declared delivery states overlap; neither account action is executed."""
    import copy
    rows,cfg,now=scenario(settings,'stable_growth');cfg['accounts'][0]['scene']='live'
    result=[]
    for name,product in (('universal','universal'),('standard','standard')):
        for original in rows:
            row=copy.deepcopy(original);row.update(id='demo-live-'+name+'-'+row['id'],entity_id='demo-live-'+name,promotion_type=product)
            row['source']['series_key']='demo-live-'+name
            row['context']={'created_at':(now-timedelta(days=4)).isoformat(),'plan_id':row['entity_id'],
                'aweme_id':'demo-live-aweme','plan_marketing_goal':'LIVE_PROM_GOODS','plan_status_at':row['source']['captured_at'],
                'plan_delivery_state':'delivering','is_primary_plan':name=='universal','manual_reviewed':True,'model_stable':True}
            result.append(row)
    return result,cfg,now


def roi_recovery_scenario(settings):
    """A down-to-3.15 report, one observed step to 3.31, and a 3.5 return candidate."""
    import copy
    cfg=copy.deepcopy(settings);cfg['accounts'][0].update(target_roi=2.5,roi_basis='overall_payment')
    account=cfg['accounts'][0];now=now_cn().replace(hour=16,minute=0,second=0,microsecond=0)
    root_after=now-timedelta(hours=2,minutes=20);root_before=root_after-timedelta(minutes=10)
    step_before=root_after+timedelta(hours=1);step_after=step_before+timedelta(minutes=10)
    rows=[];spend=100;revenue=320;orders=10
    for n in range(28):
        at=root_before-timedelta(hours=2)+timedelta(minutes=10*n)
        if at>root_after:spend+=30;revenue+=96;orders+=3
        goal=3.5 if at<=root_before else 3.15 if at<=step_before else 3.31
        changed=step_after if at>=step_after else root_after if at>=root_after else None
        rows.append({'id':f'demo-roi-recovery-{n}','account_id':account['id'],'entity_id':'demo-roi-recovery','scope':'plan',
            'scene':account['scene'],'promotion_type':'universal','metric_basis':'overall_payment',
            'source':{'kind':'demo','series_key':'demo-roi-recovery','captured_at':at.isoformat(),'data_at':at.isoformat(),
                      'metric_date':at.date().isoformat(),'metric_period':'day','coverage':'report'},
            'metrics':{'spend':spend,'gmv':revenue,'orders':orders,'budget':2000,'control_spend':0,'target_roi':goal,'target_cost':20},
            'quality':{'issues':[]},'control':{'running':True,'lifecycle':'mature','history_known':True,'guarantee_verified':True,
                'last_change_at':changed.isoformat() if changed else None,'changes_today':2 if at>=step_after else 1 if at>=root_after else 0,
                'budget_original':2000,'budget_increase_today':0},
            'context':{'created_at':(now-timedelta(days=4)).isoformat(),'plan_id':'demo-roi-recovery','model_stable':True,'manual_reviewed':True,
                'target_cost_basis':'overall_payment','roi_recovery_event_id':'demo-roi-root','plan_roi_basis':'overall_payment',
                'plan_roi_mode':'roi_target','plan_roi_step':.01,'plan_roi_min':.1,'plan_roi_max':100,
                'plan_roi_config_verified':True,'plan_roi_observed_at':at.isoformat()}})
    def event(identifier,action,before,after,a,b):
        return {'id':identifier,'account_id':account['id'],'entity_id':'demo-roi-recovery','scope':'plan','action':action,
            'metric_basis':'overall_payment','source_key':'demo-roi-recovery','source':'simulation','status':'simulated',
            'before_at':before.isoformat(),'after_at':after.isoformat(),'before':{'target_roi':a},'after':{'target_roi':b}}
    root=event('demo-roi-root','roi_target_down',root_before,root_after,3.5,3.15)
    step=event('demo-roi-step-1','roi_target_up',step_before,step_after,3.15,3.31);step['roi_recovery_root_id']=root['id']
    return rows,cfg,now,[root,step]


def roi_recovery_cross_day_scenario(settings):
    """Independent synthetic daily closing/opening counters, never a real import."""
    import copy
    from .schema import parse_time
    rows,cfg,old_now,events=roi_recovery_scenario(settings)
    now=old_now.replace(hour=1,minute=40);shift=now-old_now
    for event in events:
        for key in ('before_at','after_at'):event[key]=(parse_time(event[key])+shift).isoformat()
    for row in rows:
        for key in ('captured_at','data_at'):row['source'][key]=(parse_time(row['source'][key])+shift).isoformat()
        at=parse_time(row['source']['data_at']);row['source']['metric_date']=at.date().isoformat()
        for key in ('created_at','plan_roi_observed_at'):row['context'][key]=(parse_time(row['context'][key])+shift).isoformat()
        if row['control']['last_change_at']:row['control']['last_change_at']=(parse_time(row['control']['last_change_at'])+shift).isoformat()
        row['control']['changes_today']=sum(parse_time(e['after_at']).date()==at.date() and parse_time(e['after_at'])<=at for e in events)
    midnight=now.replace(hour=0,minute=0)
    opening=next(r for r in rows if parse_time(r['source']['data_at'])==midnight)
    offsets={k:opening['metrics'][k] for k in ('spend','gmv','orders')}
    closing=copy.deepcopy(opening);closing['id']='demo-daily-closing'
    closing['source']['metric_date']=(midnight-timedelta(days=1)).date().isoformat()
    closing['context'].update(day_boundary_role='closing',day_boundary_verified=True)
    for row in rows:
        if parse_time(row['source']['data_at'])>=midnight:
            for key,value in offsets.items():row['metrics'][key]-=value
    opening['context'].update(day_boundary_role='opening',day_boundary_verified=True)
    rows.append(closing)
    return rows,cfg,now,events


def cold_start_scenario(settings):
    """Positive cold image-text test; all ranges and declarations are synthetic."""
    rows,cfg,now=scenario(settings,'stable_growth')
    cfg['accounts'][0]['scene']='product'
    for n,row in enumerate(rows):
        row.update(id=f'demo-cold-{n}',entity_id='demo-cold-plan',scene='product')
        row['source']['series_key']='demo-cold-plan'
        row['metrics'].update(spend=n*10,gmv=n*30,roi=3,orders=n*3,clicks=n*40,impressions=n*1000,
                              budget=300,target_cost=10,industry_ctr=3)
        row['control'].update(lifecycle='cold',budget_original=300)
        row['context']={'created_at':(now-timedelta(days=1)).isoformat(),'plan_id':'demo-cold-plan','content_format':'image_text',
            'manual_reviewed':True,'model_stable':True,'compliance_ok':True,'plan_budget_period':'day','plan_budget_period_verified':True,
            'budget_mode':'specified','cold_min_test_spend':200,'cold_test_threshold_verified':True,'target_cost_basis':'overall_payment',
            'industry_baseline_at':(now-timedelta(days=2)).isoformat(),'industry_baseline_verified':True,
            'industry_id':'demo-women-top','baseline_industry_id':'demo-women-top','industry_baseline_source':'synthetic benchmark'}
    return rows,cfg,now


def roi_tuning_scenario(settings):
    """Two synthetic plans separate assessment ROI from their current setting."""
    import copy
    up,cfg,now=scenario(settings,'low_roi')
    down,_,_=scenario(settings,'stable_growth')
    account_id=cfg['accounts'][0]['id']
    for name,rows in (('up',up),('down',down)):
        for n,row in enumerate(rows):
            row['id']=f'demo-roi-{name}-{n}';row['entity_id']='demo-roi-'+name
            row['source']['series_key']='demo-roi-'+name
            row['metrics'].update(target_roi=3.5,target_cost=6)
            row['context']={'manual_reviewed':True,'model_stable':True,'plan_id':row['entity_id'],
                'plan_roi_basis':'overall_payment','plan_roi_mode':'roi_target','plan_roi_step':.01,
                'plan_roi_min':.01,'plan_roi_max':100,'plan_roi_config_verified':True,
                'plan_roi_observed_at':row['source']['captured_at'],'account_source_key':'demo-roi-account'}
            if name=='up' and n>18:
                row['metrics']['spend']+=10*(n-18);row['metrics']['gmv']+=11*(n-18)
            if name=='down' and n>12:
                row['metrics'].update(down[12]['metrics'])
    account=copy.deepcopy(down[-1]);account.update(id='demo-roi-account',entity_id=account_id,scope='account')
    account['source']['series_key']='demo-roi-account'
    account['metrics'].update(spend=10000,gmv=40000,roi=4,orders=1000,control_spend=0)
    account['context']={}
    return up+down+[account],cfg,now


def outcome_scenario(settings):
    """A reported simulated bid drop followed by a 60% spend-speed decline."""
    import copy
    now=now_cn().replace(hour=16,minute=0,second=0,microsecond=0)
    cfg=copy.deepcopy(settings);account=cfg['accounts'][0]
    account.update(target_roi=2.5,roi_basis='overall_payment')
    before=now-timedelta(hours=2,minutes=10);after=now-timedelta(hours=2)
    rows=[];spend=0
    for n in range(26):
        at=before-timedelta(hours=2)+timedelta(minutes=10*n)
        if n:spend+=40 if at>after else 100
        rows.append({'id':f'demo-outcome-{n}','account_id':account['id'],'entity_id':'demo-standard-plan','scope':'plan',
            'scene':'live','promotion_type':'standard','metric_basis':'overall_payment',
            'source':{'kind':'demo','series_key':'demo-outcome-report','captured_at':at.isoformat(),'data_at':at.isoformat(),
                      'metric_date':at.date().isoformat(),'metric_period':'day','coverage':'report'},
            'metrics':{'spend':spend,'gmv':spend*3,'orders':n*2,'bid':100 if at<=before else 95,'budget':2000,'control_spend':0},
            'quality':{'issues':[]},'control':{'history_known':True,'guarantee_verified':True,'running':True,
                 'last_change_at':after.isoformat(),'changes_today':1,'budget_original':2000,'budget_increase_today':0,'lifecycle':'mature'}})
    event={'id':'demo-bid-change','account_id':account['id'],'entity_id':'demo-standard-plan','scope':'plan','action':'bid_down',
           'metric_basis':'overall_payment','source_key':'demo-outcome-report','source':'simulation','status':'simulated',
           'before_at':before.isoformat(),'after_at':after.isoformat(),'before':{'bid':100},'after':{'bid':95}}
    return rows,cfg,now,[event]


def migration_scenario(settings):
    """A complete two-stage synthetic handover; never stored by the server."""
    import copy
    from .evaluation import evaluate_all
    from .migration import create_migration
    source,cfg,created=scenario(settings,'low_roi')
    target,_,_=scenario(settings,'stable_growth')
    for name,rows in (('source',source),('target',target)):
        for row in rows:
            row.update(entity_id='demo-migration-'+name,scene='product',promotion_type='chengfang')
            row['id']='migration-'+name+'-'+row['id'];row['source']['series_key']='demo-migration-'+name
            row['context']={'model_stable':True,'creative_replacement_failed':True,'bid_maintenance_reviewed':True}
            row['control']['lifecycle']='declining' if name=='source' else 'growing'
    source,refresh=synthetic_maintenance_history(source)
    rows=source+target;events=[refresh]
    plan=create_migration(source[-1]['id'],target[-1]['id'],rows,cfg,evaluate_all(rows,cfg,created,events=events),created)
    for name,original,minutes,roi in (('source',source,10,1.1),('target',target,80,4)):
        leg=plan[name];changed=created+timedelta(minutes=minutes);last=original[-1]
        for n in range(1,15):
            row=copy.deepcopy(last);at=created+timedelta(minutes=10*n)
            row['id']+='-post-'+str(n);row['source'].update(captured_at=at.isoformat(),data_at=at.isoformat())
            if 'creative_ids' in row.get('context',{}):row['context']['creative_inventory_at']=at.isoformat()
            spend=last['metrics']['spend']+30*n
            row['metrics'].update(spend=spend,gmv=spend*roi,roi=roi,orders=last['metrics']['orders']+3*n,
                                  impressions=last['metrics']['impressions']+300*n,clicks=last['metrics']['clicks']+15*n,
                                  budget=leg['after'] if at>=changed else leg['before'])
            if at>=changed:
                row['control'].update(last_change_at=changed.isoformat(),changes_today=1,
                                      budget_increase_today=plan['amount'] if name=='target' else 0)
            rows.append(row)
        events.append({'id':'demo-migration-'+name+'-report','account_id':plan['account_id'],'entity_id':leg['entity_id'],
                       'scope':'plan','action':'budget_down' if name=='source' else 'budget_up','metric_basis':plan['metric_basis'],
                       'source_key':leg['series_key'],'source':'simulation','status':'simulated',
                       'before_at':(changed-timedelta(minutes=10)).isoformat(),'after_at':changed.isoformat(),
                       'before':{'budget':leg['before']},'after':{'budget':leg['after']}})
    return rows,cfg,created+timedelta(minutes=140),events,[plan]


def synthetic_maintenance_history(rows):
    """Prepare a self-contained synthetic refresh report/history for a demo plan."""
    import copy
    from .schema import parse_time
    rows=copy.deepcopy(rows);start=parse_time(rows[0]['source']['data_at']);after=start+timedelta(minutes=10)
    reviewed=after+timedelta(hours=2,minutes=10);event_id='demo-maintenance-'+rows[0]['entity_id']
    for row in rows:
        at=parse_time(row['source']['data_at'])
        ctx=row.setdefault('context',{});ctx.update(is_primary_plan=False,decline_started_at=start.isoformat(),
            creative_inventory_verified=True,creative_inventory_at=at.isoformat(),creative_ids=['old-material']+(['new-material'] if at>=after else []),
            creative_replacement_failed=at>=reviewed,bid_maintenance_reviewed=at>=reviewed)
        if at>=after:
            ctx['creative_refresh_event_id']=event_id
            row['control'].update(last_change_at=after.isoformat(),changes_today=1)
        if at>=reviewed:ctx['bid_maintenance_review_at']=reviewed.isoformat()
    first=rows[0];simulated=first['source']['kind']=='demo'
    event={'id':event_id,'account_id':first['account_id'],'entity_id':first['entity_id'],'scope':'plan',
        'action':'refresh_creatives','metric_basis':first['metric_basis'],'source_key':first['source']['series_key'],
        'source':'simulation' if simulated else 'manual_verified_report','status':'simulated' if simulated else 'reported_applied',
        'before_at':start.isoformat(),'after_at':after.isoformat(),'before':{'material_ids':['old-material']},
        'after':{'material_ids':['old-material','new-material']}}
    return rows,event


def maintenance_scenario(settings):
    """An observed failed synthetic refresh, followed by a declared bid review."""
    rows,cfg,now=scenario(settings,'low_roi')
    for row in rows:row['control']['lifecycle']='declining'
    rows,event=synthetic_maintenance_history(rows)
    return rows,cfg,now,[event]


def emergency_scenario(settings):
    """Independent synthetic minute stop and user-declared score thresholds."""
    import copy
    background,cfg,now=scenario(settings,'stable_growth')
    template=background[-1];rows=list(background)
    for n in range(21):
        row=copy.deepcopy(template);at=now-timedelta(minutes=20-n);spend=100+min(n,10)*10
        row.update(id='demo-emergency-minute-'+str(n),entity_id='demo-primary-plan')
        row['source'].update(data_at=at.isoformat(),captured_at=at.isoformat(),series_key='demo-primary-minute')
        row['metrics'].update(spend=spend,gmv=spend*3,orders=n,impressions=100+min(n,10)*100)
        row['context']={'is_primary_plan':True,'live_ongoing':True,'maintenance_active':False,
                        'low_latency_verified':True,'report_granularity':'minute'}
        rows.append(row)
    for n in range(2):
        row=copy.deepcopy(template);at=now-timedelta(minutes=10*(1-n))
        row.update(id='demo-emergency-score-'+str(n),scope='account',entity_id=cfg['accounts'][0]['id'])
        row['source'].update(data_at=at.isoformat(),captured_at=at.isoformat(),series_key='demo-account-score')
        row['context']={'score_thresholds_verified':True}
        row['metrics'].update(account_score=80 if n==0 else 55,account_score_threshold=60,
                              reputation_score=4.7 if n==0 else 4.3,reputation_threshold=4.4)
        rows.append(row)
    return rows,cfg,now


def quality_scenario(settings):
    """Paired synthetic user-quality windows plus a competing growth proposal."""
    import copy
    rows,cfg,now=scenario(settings,'stable_growth')
    row=copy.deepcopy(rows[-1]);row.update(id='demo-quality-live',scope='live',entity_id='demo-quality-room')
    row['source'].update(series_key='demo-quality-window-report',metric_period='live_session',
                         period_start=(now-timedelta(hours=4)).isoformat())
    row['context']={'live_ongoing':True,'quality_metrics_verified':True,'quality_baseline_verified':True,
        'quality_baseline_source':'独立合成的上一等长窗口；没有真实业务数据',
        'quality_definition_id':'demo-users-v1','baseline_quality_definition_id':'demo-users-v1',
        'quality_segment_id':'demo-room-all','baseline_quality_segment_id':'demo-room-all',
        'quality_baseline_start':(now-timedelta(hours=1)).isoformat(),
        'quality_baseline_end':(now-timedelta(minutes=30)).isoformat(),
        'quality_window_start':(now-timedelta(minutes=30)).isoformat(),'quality_window_end':now.isoformat()}
    row['metrics'].update(watch_duration=30,baseline_watch_duration=60,interaction_rate=7,baseline_interaction_rate=12,
                          order_rate=3,baseline_order_rate=8,quality_sample_users=450,baseline_quality_sample_users=500)
    return rows+[row],cfg,now
