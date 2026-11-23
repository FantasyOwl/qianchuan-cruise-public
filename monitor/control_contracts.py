"""Task-specific request/response schemas; no network or account operations."""
import json
from decimal import Decimal

from .actuation import amount, exact_id
from .schema import parse_time


def validate_control_parent(snapshot, snapshots, evaluation, settings, now):
    if not isinstance(snapshots,list) or not isinstance(evaluation,dict):
        raise ValueError('任务请求须提供本轮完整评估及母计划关联证据')
    ctx = snapshot.get('context',{})
    parent_id, source_key = ctx.get('parent_plan_id'),ctx.get('parent_source_key')
    exact_id(parent_id)
    if not isinstance(source_key,str) or not source_key:raise ValueError('任务缺少明确母计划来源键')
    matches = [e for e in evaluation.get('entities',[]) if e.get('account_id')==snapshot['account_id']
               and e.get('scope')=='plan' and e.get('entity_id')==parent_id
               and e.get('source',{}).get('series_key')==source_key]
    if len(matches)!=1:raise ValueError('母计划必须为同账户、实际ID及来源完全匹配的唯一对象')
    entity = matches[0]
    parent = next((r for r in snapshots if r.get('id')==entity.get('latest_snapshot_id')),None)
    if not parent:raise ValueError('缺少母计划当前快照')
    src = parent.get('source',{})
    at, task_at = parse_time(src.get('data_at')),parse_time(snapshot.get('source',{}).get('data_at'))
    from .engine import _settings
    max_age = _settings(settings)[0]['max_age_seconds']
    clocks = [s for s in evaluation.get('collection_health',{}).get('sources',[])
              if s.get('account_id')==snapshot['account_id'] and s.get('scope')=='plan'
              and s.get('entity_id')==parent_id and s.get('source_key')==entity.get('source_key')]
    if (parent.get('promotion_type')!='universal' or src.get('kind')!='import' or src.get('coverage')!='report'
            or src.get('metric_period','day')!='day' or src.get('metric_date')!=now.date().isoformat()
            or not -120 <= (now-at).total_seconds() <= max_age or abs((at-task_at).total_seconds())>300
            or parent.get('quality',{}).get('issues') or len(clocks)!=1 or clocks[0].get('clock_current') is not True
            or clocks[0].get('snapshot_id')!=parent.get('id')):
        raise ValueError('母计划产品、时间、范围或数据质量未满足任务请求条件')
    return parent


def prepare_control_body(proposal, snapshot, advertiser_id, task_id):
    ctx = snapshot.get('context', {})
    params = proposal.get('parameters', {})
    if snapshot.get('promotion_type') != 'universal':
        raise ValueError('当前调控任务合同仅核验全域产品，不外推其他投放产品')
    parent = ctx.get('parent_plan_id')
    exact_id(parent)
    if params.get('parent_plan_id') != parent or proposal.get('target_id', snapshot['entity_id']) != snapshot['entity_id']:
        raise ValueError('任务实际ID、母计划及建议参数必须明确一致')
    if ctx.get('manual_reviewed') is not True or snapshot.get('control', {}).get('running') is not True:
        raise ValueError('任务对象需明确人工复核且当前开启')
    kind = ctx.get('control_type')
    if proposal['action'] == 'pause_control':
        if kind not in {'material_chase','one_click_boost'}:
            raise ValueError('当前暂停合同不用于一键控量或未知工具')
        if proposal.get('before') is not True or proposal.get('after') is not False:
            raise ValueError('暂停任务须明确true到false的状态方向')
        return 'control_status', json.dumps({'advertiser_id':advertiser_id,'task_ids':[task_id],'opt_type':'PAUSE'},separators=(',',':'))
    if proposal['action'] != 'control_budget_up' or kind != 'material_chase':
        raise ValueError('任务追加预算仅支持已复核的素材追投任务')
    if ctx.get('latest_budget_verified') is not True or ctx.get('parent_control_inventory_verified') is not True:
        raise ValueError('当前任务预算与母计划追投清单尚未核验')
    before, after = amount(proposal.get('before')), amount(proposal.get('after'))
    if before != amount(snapshot.get('metrics', {}).get('budget')):
        raise ValueError('任务预算旧值与当前快照不一致')
    if not Decimal(0) < (after-before)/before <= Decimal('.5'):
        raise ValueError('任务单次追加须为正数且不得超过50%用户策略')
    evidence = proposal.get('evidence', {})
    parent_budget = amount(evidence.get('parent_budget'))
    parent_total = amount(snapshot.get('metrics', {}).get('parent_chase_budget_total'))
    if (amount(evidence.get('current_budget')) != before or amount(evidence.get('new_budget')) != after
            or amount(evidence.get('parent_chase_budget_total')) != parent_total or parent_total < before):
        raise ValueError('任务及母计划预算证据与当前值不一致')
    if after > parent_budget:
        raise ValueError('任务预算不得超过全域母计划预算')
    if after > parent_budget*Decimal('.3') or parent_total+after-before > parent_budget*Decimal('.3'):
        raise ValueError('任务及同母计划追投预算合计不得超过母计划预算30%的用户策略')
    body = ('{"advertiser_id":'+str(advertiser_id)+',"update_budget_infos":[{"task_id":'
            +str(task_id)+',"budget":'+format(after,'.2f')+'}]}')
    return 'control_budget', body


def _expected(ids):
    result = {str(exact_id(str(x))) for x in ids}
    if not 1 <= len(result) <= 10:
        raise ValueError('本地任务回执仅处理1至10个明确任务')
    return result


def _id(value):
    if type(value) not in {str,int}:
        raise ValueError('任务ID必须无损')
    key = str(exact_id(str(value)))
    if key != str(value):raise ValueError('任务ID须为规范十进制')
    return key


def _result(ids, states):
    confirmed = sorted(i for i in ids if states.get(i) == 'SUCCESS')
    failed = sorted(i for i in ids if states.get(i) == 'FAILED')
    unknown = sorted(ids-set(confirmed)-set(failed))
    return {'success':not failed and not unknown,'confirmed':confirmed,'failed':failed,'unknown':unknown,'executed':False}


def _envelope(value, ids):
    unknown = _result(ids,{})
    if not isinstance(value,dict) or type(value.get('code')) is not int:
        return None, unknown
    if value['code'] != 0:
        return None, _result(ids,{i:'FAILED' for i in ids})
    if not isinstance(value.get('data'),dict):return None,unknown
    return value['data'],unknown


def interpret_control_budget_response(value, expected_ids):
    ids = _expected(expected_ids);data, fallback = _envelope(value,ids)
    if data is None:return fallback
    rows = data.get('results')
    if not isinstance(rows,list):return fallback
    states = {}
    for row in rows:
        if not isinstance(row,dict):return fallback
        try:key = _id(row.get('task_id'))
        except ValueError:return fallback
        if key not in ids:return fallback
        if key in states or type(row.get('status')) is not bool:
            states[key] = 'UNKNOWN'
        elif row['status'] and row.get('error_message') not in (None,''):
            states[key] = 'UNKNOWN'
        else:states[key] = 'SUCCESS' if row['status'] else 'FAILED'
    return _result(ids,states)


def interpret_control_status_response(value, expected_ids):
    ids = _expected(expected_ids);data, fallback = _envelope(value,ids)
    if data is None:return fallback
    successes, errors = data.get('task_ids',[]), data.get('errors',[])
    if not isinstance(successes,list) or not isinstance(errors,list):return fallback
    states = {}
    for item in successes:
        try:key = _id(item)
        except ValueError:return fallback
        if key not in ids:return fallback
        states[key] = 'UNKNOWN' if key in states else 'SUCCESS'
    for row in errors:
        if not isinstance(row,dict):return fallback
        try:key = _id(row.get('task_id'))
        except ValueError:return fallback
        if key not in ids:return fallback
        states[key] = 'UNKNOWN' if key in states else 'FAILED'
    return _result(ids,states)
