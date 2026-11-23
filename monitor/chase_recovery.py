"""Readback isolation is never a success receipt or released advertising budget."""
import json
from decimal import Decimal
from .actuation import amount
from .schema import parse_time

UNCERTAIN={'submitting','unknown','submitted_pending_verification'}

def held_native_budget(intent):
    """Conservative occupancy for a held native budget edit or task pause."""
    if intent.get('task_scene')!='MATERIAL_ADD_BUDGET':return None
    try:
        if intent.get('action')=='task_budget':value=max(amount(intent.get('before')),amount(intent.get('after')))
        elif intent.get('action')=='pause_task' and intent.get('before') is True and intent.get('after') is False:
            snapshot=intent.get('browser_surface') or intent.get('task_before') or {}
            value=amount(snapshot.get('budget'))
        else:return None
    except (ValueError,TypeError):return None
    return value if value>0 else None


def reservations(db,aid,pid):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hybrid_runs'").fetchone():return []
    out=[]
    for row in db.execute("SELECT a.id,a.state,a.intent,a.result,a.created_at,h.data FROM api_actions a JOIN hybrid_runs h ON json_extract(h.data,'$.action_id')=a.id WHERE h.account_id=? AND h.plan_id=? AND json_extract(h.data,'$.isolated_readback')=1 AND h.state NOT IN ('completed','cancelled')",(aid,pid)):
        i=json.loads(row['intent']);d=json.loads(row['data']);p=d.get('permit') or {}
        if row['state'] not in UNCERTAIN or i.get('account_id')!=aid or i.get('plan_id')!=pid or i.get('action')!='material_chase' or i.get('execution_channel')!='browser' or d.get('phase')!='verify' or p.get('consumed') is not True or p.get('action_id')!=row['id']:continue
        existing=set(d.get('tasks',{}));original=json.loads(row['result'] or '{}')
        original_tid=str(original.get('task_id') or (original.get('reply') or {}).get('id') or '')
        # The pre-submit inventory is only a snapshot. Later, independently
        # verified creations with a different exact task name are also known
        # independent tasks; a frozen snapshot must not block them forever.
        for known in db.execute("SELECT id,intent,result FROM api_actions WHERE state='verified' AND id!=? AND json_extract(intent,'$.account_id')=? AND json_extract(intent,'$.plan_id')=? AND json_extract(intent,'$.action')='material_chase'",(row['id'],aid,pid)):
            ki=json.loads(known['intent']);kr=json.loads(known['result'] or '{}')
            name=ki.get('browser_batch',{}).get('task_name') or ki.get('body',{}).get('name')
            tid=str(kr.get('task_id') or (kr.get('reply') or {}).get('id') or '')
            if name and name!=i['browser_batch']['task_name'] and tid.isascii() and tid.isdecimal() and tid!=original_tid and (kr.get('independently_verified') is True or kr.get('ad_changes')==1):existing.add(tid)
        out.append({'action_id':row['id'],'budget':amount(i['body']['budget']),'material_ids':set(i['material_ids']),
                    'existing_task_ids':existing,'task_name':i['browser_batch']['task_name'],
                    'created_at':row['created_at'],'original_task_id':original_tid,
                    'delivery_mode':{'SMART_BID_CUSTOM':'cost_control','SMART_BID_CONSERVATIVE':'volume'}.get(i['body'].get('smart_bid_type'))})
    return out

def independent(reservation,intent):
    if intent['action']=='auto_rule_scan':return True
    if intent['action']=='material_chase':return not reservation['material_ids'].intersection(intent.get('material_ids',[]))
    return intent['action'] in {'pause_task','resume_task','task_budget','task_duration'} and intent.get('task_id') in reservation['existing_task_ids']

def native_task_independent(db,reservation,intent,run_id,now):
    """Recognize a different native task without resolving/replaying the old write.

    Only a validated observation stored by this active rule run is evidence.
    A different name alone is insufficient: creation time and an incompatible
    material count or delivery mode must also distinguish the tasks.
    """
    if not run_id or intent.get('execution_channel')!='browser' or intent['action'] not in {'pause_task','task_budget','task_duration'}:return False
    tid=intent.get('task_id','')
    if not tid or not tid.isascii() or not tid.isdecimal() or tid==reservation['original_task_id']:return False
    r=db.execute("SELECT data FROM hybrid_rule_runs WHERE id=? AND account_id=? AND plan_id=? AND state NOT IN ('completed','cancelled')",(run_id,intent['account_id'],intent['plan_id'])).fetchone()
    if not r:return False
    d=json.loads(r['data']);s=d.get('rows',{}).get(tid,{})
    if d.get('preview') or (s.get('account_id'),s.get('plan_id'),s.get('task_id'))!=(intent['account_id'],intent['plan_id'],tid):return False
    if not s.get('name') or s['name']==reservation['task_name']:return False
    c=s.get('context') or {}
    try:
        if not 0<=(now-parse_time(s.get('captured_at'))).total_seconds()<=90:return False
        if (parse_time(c.get('created_at'))-parse_time(reservation['created_at'])).total_seconds()<=120:return False
    except (ValueError,TypeError):return False
    if c.get('control_type')!='material_chase':return False
    count=s.get('material_count');mode=c.get('control_delivery_mode')
    return (type(count) is int and count>0 and count!=len(reservation['material_ids'])) or (
        mode in {'cost_control','volume'} and reservation['delivery_mode'] in {'cost_control','volume'} and mode!=reservation['delivery_mode'])

def reserved_total(db,aid,pid):
    return sum((r['budget'] for r in reservations(db,aid,pid)),Decimal(0))
