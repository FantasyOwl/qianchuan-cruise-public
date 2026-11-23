"""Fictitious four-hour base ledger plus reviewed native setting declarations."""
import copy
from .demo import control_creation_scenario
from .evaluation import evaluate_all
from .material_chase_creation import FORMAT,preview_material_chase


def chase_creation_scenario(settings):
    parent_rows,cfg,at=control_creation_scenario(settings);account=parent_rows.pop();rows=[]
    for n,parent in enumerate(parent_rows):
        parent=copy.deepcopy(parent);spend=n*100
        parent['metrics'].update(spend=spend,gmv=spend*2.5,orders=n*2,clicks=n*40,impressions=n*1000,
            parent_chase_budget_total=0,active_heated_creatives=0)
        parent['context'].update(parent_control_inventory_verified=True,creative_inventory_verified=True,
            creative_inventory_at=parent['source']['captured_at'])
        rows.append(parent);creative=copy.deepcopy(parent)
        creative.update(id='demo-chase-creative-'+str(n),scope='creative',entity_id='7333333333333333')
        creative['source']['series_key']='demo-chase-base'
        creative['metrics'].update(parent_budget=10000,base_spend=spend,base_gmv=spend*2.5,base_orders=n*2,
            base_clicks=n*40,base_impressions=n*1000)
        creative['context'].update(parent_plan_id=parent['entity_id'],parent_source_key=parent['source']['series_key'],
            material_id='7333333333333333',creative_parent_verified=True,creative_type='custom',
            chase_history_known=True,differentiated_targeting_verified=True)
        rows.append(creative)
    rows.append(account);evaluation=evaluate_all(rows,cfg,at)
    proposal=next(p for p in evaluation['recommendations'] if p['action']=='create_chase' and p['eligible'])
    payload={'format':FORMAT,'account_id':'9990001','proposal_id':proposal['id'],'creative_snapshot_id':rows[-2]['id'],
        'parent_plan_id':'7333333333333333','parent_snapshot_id':rows[-3]['id'],'conversion_bid':10,
        'material':{'material_id':'7333333333333333','native':{'aweme_item_id':'7333333333333333'},'snapshot_id':rows[-2]['id'],
            'material_type':'VIDEO','material_status':'DELIVERY_OK','audit_status':'PASS','is_delete':False,
            'mapping_verified':True,'observed_at':at.isoformat()},
        'audience':{'aweme_fan_accounts':['7333333333333333'],'aweme_fan_behaviors':['GOODS_CARTS_ORDER'],'aweme_fan_behaviors_days':'DAYS_30'},
        'targeting_review':{'reviewed':True,'observed_at':at.isoformat(),'parent_snapshot_id':rows[-3]['id'],'method':'reviewed_fan_audience'}}
    return rows,cfg,at,payload


def chase_creation_example(settings):
    rows,cfg,at,payload=chase_creation_scenario(settings)
    result=preview_material_chase(payload,rows,cfg,evaluate_all(rows,cfg,at),at,synthetic=True)
    result['note']='全部使用虚构账户与声明；没有创建任务、注册截止、写入快照或调用原生客户端。'
    return result
