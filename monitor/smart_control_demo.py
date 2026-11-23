"""Fictitious flow-control preview, without persistence, clients or timers."""
from datetime import timedelta
from .demo import control_creation_scenario
from .smart_control import prepare_smart_control, CREATE_PATH, STOP_PATH, CREATE_SOURCE, STOP_SOURCE


def smart_control_scenario(settings):
    rows,cfg,at=control_creation_scenario(settings)
    rows[-2]['context'].update(carrying_ready=False,
        predicted_overload_at=(at+timedelta(minutes=4)).isoformat(),
        smart_control_budget=100,smart_control_budget_reviewed=True,latest_budget_verified=True)
    return rows,cfg,at


def smart_control_preview(rows,cfg,at,evaluation):
    proposal=next((p for p in evaluation['recommendations'] if p['action']=='one_click_control' and p['eligible']),None)
    if proposal is None:raise ValueError('合成控量场景没有当前合格建议')
    parent=next(r for r in rows if r['id']=='demo-create-24')
    planned=prepare_smart_control(proposal,parent,cfg,at,rows,evaluation,synthetic=True)
    return {'synthetic':True,'account_id':proposal['account_id'],'parent_plan_id':proposal['entity_id'],
        'budget_example_only':True,'budget':'100.00','local_duration_policy':planned['local_duration_policy'],
        'create_request':{'method':'POST','url':None,'documented_endpoint':CREATE_PATH,
            'official_source':CREATE_SOURCE,'body_json':planned['body_json'],'submitted':False},
        'stop_request':{'method':'POST','url':None,'documented_endpoint':STOP_PATH,
            'official_source':STOP_SOURCE,'body_json':planned['stop_body_json'],'submitted':False},
        'execution_blockers':planned['execution_blockers'],'persisted':False,'executed':False,
        'native_client_enabled':False,'deadline_registered':False,'state_readback_verified':False}
