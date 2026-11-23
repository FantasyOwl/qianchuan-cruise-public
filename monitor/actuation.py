"""Verified request contracts, local preview only. This module has no network IO.

Contract availability is distinct from data eligibility and from authorization.
The current user's authorization is observation only, so execution is denied
regardless of the contents of a proposal or configuration file.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from datetime import timedelta

from .schema import now_cn, parse_time

BASE_URL = "https://api.oceanengine.com"
CONTRACTS = {
    "budget": {"path": "/open_api/v1.0/qianchuan/uni_promotion/ad/budget/update/",
               "source": "https://open.oceanengine.com/labels/12/docs/1841395352172800"},
    "status": {"path": "/open_api/v1.0/qianchuan/uni_promotion/ad/status/update/",
               "source": "https://open.oceanengine.com/labels/12/docs/1804364027501580?origin=left_nav"},
    "roi": {"path": "/open_api/v1.0/qianchuan/uni_promotion/ad/roi2_goal/update/",
            "source": "https://open.oceanengine.com/labels/12/docs/1841394087572811"},
    "control_budget": {"path":"/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/budget/update/",
                       "source":"https://open.oceanengine.com/labels/12/docs/1848753215997963"},
    "control_status": {"path":"/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/status/update/",
                       "source":"https://open.oceanengine.com/labels/12/docs/1825827367287017"},
    "control_create": {"path":"/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/create/",
                       "source":"https://open.oceanengine.com/labels/12/docs/1825827435645963"},
    "smart_control_create": {"path":"/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/smart_control/create/",
                       "source":"https://open.oceanengine.com/labels/12/docs/1848756884121668"},
    "smart_control_stop": {"path":"/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/smart_control/status/update/",
                       "source":"https://open.oceanengine.com/labels/12/docs/1848757011743171"},
}


def exact_id(value):
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or not 1 <= len(value) <= 20:
        raise ValueError("对象ID必须为不超过20位的十进制字符串")
    number = int(value)
    if number <= 0 or number > 2**64-1:
        raise ValueError("对象ID超出允许范围")
    return number


def amount(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError("金额格式无效")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("金额格式无效") from None
    if not number.is_finite() or number <= 0 or number > Decimal("1000000000000000") or number.quantize(Decimal(".01")) != number:
        raise ValueError("金额必须为正数且最多两位小数")
    return number


def _wire_json(advertiser, item):
    # Construct the Decimal token directly; no intermediate binary-float roundoff.
    ad_id = item["ad_id"]
    tokens = [f'"ad_id":{ad_id}', f'"budget":{item["budget"]:.2f}']
    for key in ("min_estimate_convert", "estimate_convert", "estimate_roi_goal", "min_estimate_roi_goal"):
        if key in item:
            tokens.append(json.dumps(key)+":"+str(item[key]))
    return '{"advertiser_id":'+str(advertiser)+',"update_budget_infos":[{'+','.join(tokens)+'}]}'


def _roi_body(proposal, snapshot, advertiser, ad_id):
    context = snapshot.get('context', {})
    basis = snapshot.get('metric_basis')
    goals = {'overall_payment': 'AD_CONVERT_TYPE_LIVE_PAY_ROI', 'net': 'AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI'}
    if basis not in goals or context.get('plan_roi_basis') != basis:
        raise ValueError('ROI合同只覆盖明确的成交或净成交口径，不能套用综合营销或调控任务ROI')
    if context.get('plan_delivery_mode') != 'cost_control' or context.get('plan_roi_mode') != 'roi_target':
        raise ValueError('必须明确为控成本投放及ROI目标优化模式')
    goal = context.get('plan_optimization_goal')
    if goal != goals[basis]:
        raise ValueError('必须明确核对与考核口径一致的官方优化目标')
    if context.get('plan_roi_config_verified') is not True or snapshot.get('source', {}).get('kind') != 'import':
        raise ValueError('当前ROI配置、范围和步长须经明确导入核对')
    if snapshot.get('control', {}).get('running') is not True or context.get('manual_reviewed') is not True:
        raise ValueError('ROI调整须明确在投且已人工复核')
    observed = parse_time(context.get('plan_roi_observed_at'))
    captured = parse_time(snapshot.get('source', {}).get('captured_at'))
    if abs((observed-captured).total_seconds()) > 1:
        raise ValueError('ROI配置时间须与当前快照对应')
    before, after = amount(proposal.get('before')), amount(proposal.get('after'))
    if before != amount(snapshot.get('metrics', {}).get('target_roi')):
        raise ValueError('ROI旧值与当前计划设置不一致，不能用用户考核目标代替')
    fraction = (after-before)/before
    direction = 1 if proposal['action'] == 'roi_target_up' else -1
    if not Decimal('.05') <= abs(fraction) <= Decimal('.10') or fraction*direction <= 0:
        raise ValueError('ROI单次方向或5%至10%幅度不符合当前用户策略')
    values = []
    for key in ('plan_roi_step', 'plan_roi_min', 'plan_roi_max'):
        val = context.get(key)
        if isinstance(val, bool) or not isinstance(val, (int, float, Decimal)):
            raise ValueError('ROI步长及范围必须为明确正数')
        dec = Decimal(str(val))
        if not dec.is_finite() or not Decimal(0) < dec <= Decimal('1000000000000000'):
            raise ValueError('ROI步长或范围无效')
        values.append(dec)
    step, lower, upper = values
    if (step < Decimal('.000001') or step*Decimal(10)**6 != (step*Decimal(10)**6).to_integral_value()
            or not lower <= before <= upper or not lower <= after <= upper
            or before % step != 0 or after % step != 0):
        raise ValueError('ROI当前值或新值不在明确的输入网格及范围内')
    # The verified API allows at most two decimals. Never round a proposal to
    # fit the wire format; amount() above rejects it before constructing JSON.
    return ('{"advertiser_id":'+str(advertiser)+',"update_roi2_infos":[{"ad_id":'+str(ad_id)
            +',"roi2_goal":'+format(after, '.2f')+',"deep_external_action":'+json.dumps(goal)+'}]}')


def prepare_action(proposal, snapshot, settings, now=None, *, snapshots=None, evaluation=None):
    result = {"proposal_id": proposal.get("id"), "mode": "preview_only", "live_execution_allowed": False,
              "supported_for_preview": False, "blockers": [], "execution_blockers": ["用户当前仅授权观察，真实账户执行器保持断开"],
              "request": None, "rule_basis": "user_strategy"}
    errors = result["blockers"]
    if proposal.get('action')=='one_click_control':
        from .smart_control import EXECUTION_LIMITS,CREATE_SOURCE,STOP_SOURCE
        result['execution_blockers'].extend(EXECUTION_LIMITS)
        result['official_references']=[{'title':'创建直播一键控量','url':CREATE_SOURCE},
            {'title':'按母计划停止一键控量','url':STOP_SOURCE}]
    if proposal.get('action')=='create_chase':
        errors.extend(['直播控成本素材追投的当前接口不支持设置时长，默认长期有效；不能静默丢弃用户2至4小时要求',
            '可靠到期关停与独立读回尚未接通；商品定向和其他创建分支仍需单独核验'])
        result['official_references']=[{'title':'创建调控任务的场景、时长和定向条件','url':CONTRACTS['control_create']['source']}]
    if proposal.get('action') in {'pause_creative','replace_creative','refresh_creatives','review_cold_creative','review_duplicate_creatives'}:
        errors.extend(['当前素材建议没有已接通的暂停或自动替换执行链，不能用删除接口代替暂停',
            '官方删除合同仅支持自提素材、不支持智选素材；多号多商品复用时删除可能影响其他投放用途',
            '按日期查询的素材列表不能认证无消耗对象的完整清单，需单独核对新增、替换和影响范围'])
        result['official_references']=[{'title':'获取计划下素材及查询范围','url':'https://open.oceanengine.com/labels/12/docs/1804363488115850'},
            {'title':'添加乘方/全域计划素材','url':'https://open.oceanengine.com/labels/12/docs/1835232814536707'},
            {'title':'删除全域计划下素材及复用影响','url':'https://open.oceanengine.com/labels/12/docs/1804363891396633'}]
    cfg_accounts = {a["id"]: a for a in settings.get("accounts", []) if isinstance(a, dict) and "id" in a}
    account_id, entity_id = proposal.get("account_id"), proposal.get("entity_id")
    if account_id not in cfg_accounts or account_id != snapshot.get("account_id") or entity_id != snapshot.get("entity_id"):
        errors.append("建议对象与白名单快照不一致")
    if proposal.get("target_id", entity_id) != entity_id:
        errors.append("建议操作目标与证据对象不一致")
    is_control = proposal.get('scope') == 'control' and snapshot.get('scope') == 'control'
    if proposal.get('scope') != snapshot.get('scope') or snapshot.get('scope') not in {'plan','control'}:
        errors.append("已核验合同须明确区分计划和调控任务，不能替换为素材或其他对象")
    if snapshot.get("promotion_type") not in {"universal", "chengfang"}:
        errors.append("该产品尚未核验适用的写合同")
    if snapshot.get("source", {}).get("kind") == "demo":
        errors.append("演示对象不生成真实业务API请求")
    if proposal.get("eligible") is not True:
        errors.append("本地建议的数据与风险条件未满足")
    if proposal.get("mode") != "simulation_only":
        errors.append("建议未经过只读策略链路")
    if snapshot.get("quality", {}).get("issues"):
        errors.append("快照存在未解决的数据质量问题")
    if proposal.get("metric_basis") != snapshot.get("metric_basis"):
        errors.append("建议与快照的指标口径不一致")
    account=cfg_accounts.get(account_id,{})
    assessment_matches = account.get('roi_basis') == snapshot.get('metric_basis')
    if is_control:
        assessment_matches = (snapshot.get('metric_basis') == 'control_payment'
            and snapshot.get('context', {}).get('target_metric_basis') == account.get('roi_basis')
            and account.get('roi_basis') in {'overall_payment','control_payment'})
    if account.get("target_roi") is None or not assessment_matches:
        errors.append("用户目标ROI或考核口径尚未确认")
    source=snapshot.get("source",{})
    now=now or now_cn()
    try:
        data_at=parse_time(source.get("data_at"))
        if (now-data_at).total_seconds()>settings.get("rules",{}).get("max_age_seconds",600) or data_at>now+timedelta(minutes=2):
            errors.append("快照数据时间已过期或在未来")
        if source.get("metric_date")!=now.date().isoformat() or source.get("metric_period","day")!="day":
            errors.append("不是当前日累计数据")
    except (ValueError,TypeError):
        errors.append("缺少可核验的数据时间")
    if source.get("coverage")!="report":
        errors.append("可见页面尚未核验全量范围")
    ctl = snapshot.get("control", {})
    if ctl.get("history_known") is not True or ctl.get("guarantee_verified") is not True:
        errors.append("调控历史或当前保障限制未核验")
    try:
        advertiser, ad_id = exact_id(account_id), exact_id(entity_id)
    except ValueError as exc:
        errors.append(str(exc)); advertiser=ad_id=None
    action = proposal.get("action")
    allowed = {'control_budget_up','pause_control'} if is_control else {'budget_up','budget_down','pause_plan','resume_plan','roi_target_up','roi_target_down','one_click_boost','one_click_control'}
    if action not in allowed:
        errors.append("该动作的官方写合同尚未核验，保留语义建议")
    if errors:
        return result
    if action=='one_click_control':
        from .smart_control import prepare_smart_control
        try:planned=prepare_smart_control(proposal,snapshot,settings,now,snapshots,evaluation)
        except (ValueError,TypeError,InvalidOperation) as exc:errors.append(str(exc));return result
        body_json=planned['body_json'];contract=CONTRACTS['smart_control_create']
        result['local_duration_policy']=planned['local_duration_policy']
        stop=CONTRACTS['smart_control_stop']
        result['required_stop_request']={'method':'POST','url':BASE_URL+stop['path'],
            'body_json':planned['stop_body_json'],'official_source':stop['source'],'submitted':False,
            'state_readback_verified':False,'parent_hold_released':False}
    elif action=='one_click_boost':
        from .control_creation import prepare_boost_body
        try:body_json=prepare_boost_body(proposal,snapshot,settings,now,snapshots,evaluation)
        except (ValueError,TypeError,InvalidOperation) as exc:errors.append(str(exc));return result
        contract=CONTRACTS['control_create']
        result['execution_blockers'].append('创建回执和新任务ID不证明任务已可投；需独立读回，不能自动解除母计划占用')
    elif is_control:
        from .control_contracts import prepare_control_body, validate_control_parent
        try:
            parent = validate_control_parent(snapshot, snapshots, evaluation, settings, now)
            if action=='control_budget_up' and amount(proposal.get('evidence',{}).get('parent_budget')) != amount(parent.get('metrics',{}).get('budget')):
                raise ValueError('关联母计划当前预算与建议证据不一致')
            contract_key, body_json = prepare_control_body(proposal, snapshot, advertiser, ad_id)
        except (ValueError, TypeError, InvalidOperation) as exc:
            errors.append(str(exc));return result
        contract = CONTRACTS[contract_key]
    elif action in {"budget_up", "budget_down"}:
        try:
            before, after = amount(proposal.get("before")), amount(proposal.get("after"))
            if before != amount(snapshot.get("metrics", {}).get("budget")):
                raise ValueError("预算旧值与当前快照不符")
            fraction = (after-before)/before
            limits=settings.get("rules",{})
            if action=="budget_up" and not Decimal(0)<fraction<=Decimal(str(min(.2,limits.get("budget_step_up",.2)))):
                raise ValueError("上调幅度超出当前用户策略")
            if action=="budget_down" and not -Decimal(str(min(.3,limits.get("budget_step_down",.1))))<=fraction<Decimal(0):
                raise ValueError("下调幅度超出当前用户策略")
            if action=="budget_down" and fraction>Decimal("-.1"):
                raise ValueError("主计划预算下调应在10%到30%之间")
            context=snapshot.get("context",{})
            budget_mode=context.get("budget_mode")
            if budget_mode not in {"specified", "suggested"}:
                raise ValueError("指定预算/建议预算类型未核验，不能省略条件必填字段")
            item={"ad_id":ad_id,"budget":after}
            if budget_mode=="suggested":
                for key in ("min_estimate_convert","estimate_convert"):
                    val=snapshot.get("metrics",{}).get(key)
                    if type(val) is not int or val<0:
                        raise ValueError("建议预算缺少已核验的预估转化字段")
                    item[key]=val
                for key in ("estimate_roi_goal","min_estimate_roi_goal"):
                    val=snapshot.get("metrics",{}).get(key)
                    item[key]=amount(val)
            body_json=_wire_json(advertiser,item)
        except (ValueError, InvalidOperation, TypeError) as exc:
            errors.append(str(exc));return result
        contract=CONTRACTS["budget"]
    elif action in {'roi_target_up', 'roi_target_down'}:
        try:
            body_json = _roi_body(proposal, snapshot, advertiser, ad_id)
        except (ValueError, TypeError, InvalidOperation) as exc:
            errors.append(str(exc));return result
        contract = CONTRACTS['roi']
    else:
        before = action == "pause_plan"
        if (type(ctl.get("running")) is not bool or ctl["running"] is not before
                or type(proposal.get("before")) is not bool or proposal["before"] is not before
                or type(proposal.get("after")) is not bool or proposal["after"] is before):
            errors.append("计划启停旧值、新值或当前开启状态不一致")
            return result
        body_json=json.dumps({"advertiser_id":advertiser,"ad_ids":[ad_id],"opt_status":"DISABLE" if action=="pause_plan" else "ENABLE"},separators=(',',':'))
        contract=CONTRACTS["status"]
    result["request"]={"method":"POST","url":BASE_URL+contract["path"],"headers_required":["Access-Token","Content-Type: application/json"],
                       "body_json":body_json,"official_source":contract["source"],"submitted":False}
    result["supported_for_preview"]=True
    result["digest"]=hashlib.sha256(body_json.encode()).hexdigest()
    return result


def interpret_budget_response(value, expected_ids):
    """Decode mock or recorded responses without treating HTTP 200 as success."""
    return _interpret_update_response(value, expected_ids)


def interpret_roi_response(value, expected_ids):
    """ROI and budget use per-plan SUCCESS/FAILED, not status-update flag."""
    return _interpret_update_response(value, expected_ids)


def _interpret_update_response(value, expected_ids):
    ids = {str(exact_id(str(x))) for x in expected_ids}
    if not 1 <= len(ids) <= 10:
        raise ValueError('更新回执必须对应1至10个明确计划')
    result = {'success':False, 'confirmed':[], 'failed':[], 'unknown':sorted(ids), 'executed':False}
    if not isinstance(value, dict) or type(value.get('code')) is not int:
        return result
    if value['code'] != 0:
        result.update(failed=sorted(ids), unknown=[])
        return result
    rows = value.get('data', {}).get('results') if isinstance(value.get('data'), dict) else None
    if not isinstance(rows, list) or not rows:
        return result
    statuses = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get('ad_id')) not in {str, int}:
            return result
        try:
            key = str(exact_id(str(row['ad_id'])))
        except ValueError:
            return result
        if key not in ids or key != str(row['ad_id']):
            return result
        status = row.get('status')
        if key in statuses or not isinstance(status, str) or status not in {'SUCCESS', 'FAILED'}:
            statuses[key] = 'UNKNOWN'
        elif status == 'SUCCESS' and row.get('error_message') not in (None, ''):
            statuses[key] = 'UNKNOWN'
        else:
            statuses[key] = status
    confirmed = sorted(i for i in ids if statuses.get(i) == 'SUCCESS')
    failed = sorted(i for i in ids if statuses.get(i) == 'FAILED')
    unknown = sorted(ids-set(confirmed)-set(failed))
    return {'success':not failed and not unknown,'confirmed':confirmed,'failed':failed,'unknown':unknown,'executed':False}


def interpret_status_response(value, expected_ids):
    """The status contract uses boolean flag, never budget's status string.

    Confirmed means the API acknowledged an individual update, not that a
    subsequent independent observation has verified the actual plan state.
    """
    ids = {str(exact_id(str(item))) for item in expected_ids}
    if not 1 <= len(ids) <= 10:
        raise ValueError("启停回执必须对应1至10个明确计划")
    result = {"success": False, "confirmed": [], "failed": [], "unknown": sorted(ids), "executed": False}
    if not isinstance(value, dict) or type(value.get("code")) is not int:
        return result
    if value["code"] != 0:
        result.update(failed=sorted(ids), unknown=[])
        return result
    rows = value.get("data", {}).get("results") if isinstance(value.get("data"), dict) else None
    if not isinstance(rows, list) or not rows:
        return result
    statuses = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get("ad_id")) not in {str, int}:
            return result
        try:
            key = str(exact_id(str(row["ad_id"])))
        except ValueError:
            return result
        if key not in ids:
            # An unrelated object can mean the response belongs to another
            # request. Do not let it certify this request's expected objects.
            return result
        if key in statuses:
            statuses[key] = "UNKNOWN"
        elif type(row.get("flag")) is not bool:
            statuses[key] = "UNKNOWN"
        elif row["flag"] and row.get("error") not in (None, {}):
            statuses[key] = "UNKNOWN"
        else:
            statuses[key] = "SUCCESS" if row["flag"] else "FAILED"
    confirmed = sorted(i for i in ids if statuses.get(i) == "SUCCESS")
    failed = sorted(i for i in ids if statuses.get(i) == "FAILED")
    unknown = sorted(ids - set(confirmed) - set(failed))
    return {"success": not failed and not unknown, "confirmed": confirmed, "failed": failed,
            "unknown": unknown, "executed": False}


def execute(*args, **kwargs):
    raise PermissionError("当前任务禁止真实账户操作；本模块仅生成预览，未实现网络提交")
