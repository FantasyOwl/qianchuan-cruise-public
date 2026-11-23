"""Supplied-file example only. Never mints native IO receipts or calls IO."""
import json
from datetime import timedelta
from .demo import control_creation_scenario
from .evaluation import evaluate_all
from .control_creation import prepare_boost_body,CONTRACT_VERSION,PURCHASE
from .control_creation_readback import FORMAT,prepare_creation_read_request,review_supplied_creation
from .native_control_io import digest


def creation_readback_example(settings):
    rows,cfg,at=control_creation_scenario(settings);evaluation=evaluate_all(rows,cfg,at)
    proposal=next(p for p in evaluation['recommendations'] if p['action']=='one_click_boost' and p['eligible'])
    body=prepare_boost_body(proposal,rows[-2],cfg,at,rows,evaluation,synthetic=True)
    attempt={'created_at':at.isoformat(),'state':'unknown','context':{
        'scope':'control_create','entity_id':None,'account_id':'9990001','parent_plan_id':'7333333333333333',
        'action':'one_click_boost','control_type':'one_click_boost','contract':'control_create','contract_version':CONTRACT_VERSION,
        'marketing_goal':'LIVE_PROM_GOODS','requested_external_action':PURCHASE,'request_body_json':body,
        'request_digest':digest(body),'requested_budget':100,'requested_duration_hours':1,'cooldown_seconds':3600},
        'result':{'submitted':True,'business_accepted':True,'created_task_id':'7333333333333333',
            'parent_plan_id':'7333333333333333','creation_receipt':{'origin_verified':False}}}
    when=at+timedelta(minutes=70)
    request=prepare_creation_read_request(attempt,cfg,at-timedelta(minutes=1),when,max_pages=5,page_size=10)
    query={k:request[k] for k in ('account_id','parent_plan_id','marketing_goal','scene','task_id','window_start','window_end','max_pages','page_size')}
    query.update(request_started_at=when.isoformat(),received_at=when.isoformat())
    page={'code':0,'data':{'task_list':[{'id':7333333333333333,'budget':100,'duration':1,'task_status':'FROZEN',
        'create_time':at.strftime('%Y-%m-%d %H:%M:%S'),'external_action_name':'直播间支付','deep_external_action_name':None}],
        'page_info':{'page':1,'page_size':10,'total_number':1,'total_page':1}}}
    document={'format':FORMAT,'attempt':attempt,'query_context':query,'raw_pages':[page]}
    out=review_supplied_creation(document,cfg,when)
    out.update(synthetic=True,simulation_checked_at=when.isoformat(),example_document=document,
        note='合成文件能匹配任务配置和结束状态，但没有独立原生来源，不会解除任何母计划占用。')
    return out
