"""Local report of an observed or simulated change, never an execution command."""
import re
from datetime import timedelta
from decimal import Decimal

from .schema import now_cn,numeric,parse_time

ACTIONS={"budget_up","budget_down","control_budget_up","bid_down","bid_restore","roi_target_up","roi_target_down",
         "pause_plan","resume_plan","pause_control","pause_creative","create_chase","one_click_boost","one_click_control","refresh_creatives"}
STATES={"reported_applied","simulated","rejected","failed","unknown"}


def normalize_event(raw,settings,received_at=None):
    if not isinstance(raw,dict):raise ValueError("操作报告必须为对象")
    value={}
    for key in ("id","account_id","entity_id"):
        item=raw.get(key)
        if not isinstance(item,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}",item):raise ValueError(f"操作报告{key}必须为明确字符串ID")
        value[key]=item
    if value["account_id"] not in {a["id"] for a in settings.get("accounts",[])}:raise ValueError("报告账户不在当前配置白名单内")
    if raw.get("scope") not in {"plan","creative","control"}:raise ValueError("报告对象层级无效")
    value["scope"]=raw["scope"]
    action=raw.get("action")
    if action not in ACTIONS:raise ValueError("报告动作类型未支持")
    value["action"]=action
    if 'roi_recovery_root_id' in raw:
        link=raw['roi_recovery_root_id']
        if action!='roi_target_up' or not isinstance(link,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',link) or link==value['id']:
            raise ValueError('ROI回调报告须使用不同的明确roi_recovery_root_id，并保持roi_target_up动作')
        value['roi_recovery_root_id']=link
    source,status=raw.get("source"),raw.get("status")
    if source not in {"manual_verified_report","simulation"} or status not in STATES:raise ValueError("报告来源或状态无效")
    if status=="reported_applied" and source!="manual_verified_report" or status=="simulated" and source!="simulation":
        raise ValueError("人工观察与模拟记录的状态不能混用")
    value.update(source=source,status=status)
    basis=raw.get("metric_basis")
    if basis not in {"overall_payment","net","marketing","control_payment","settled_7d","unknown"}:raise ValueError("报告指标口径无效")
    value["metric_basis"]=basis
    key=raw.get("source_key")
    if not isinstance(key,str) or not 1<=len(key)<=1000 or any(ord(c)<32 for c in key):raise ValueError("报告必须精确关联一个数据来源键")
    value["source_key"]=key
    before,after=parse_time(raw.get("before_at")),parse_time(raw.get("after_at"))
    if before>after or after>(received_at or now_cn())+timedelta(minutes=2):raise ValueError("报告前后时间顺序无效或在未来")
    value.update(before_at=before.isoformat(),after_at=after.isoformat())
    for stage in ("before","after"):
        fields=raw.get(stage)
        if not isinstance(fields,dict):raise ValueError("报告需要明确的前后观察值")
        if action=='refresh_creatives' and any(k in fields for k in ('budget','bid','target_roi','running')):
            raise ValueError('补充素材与预算、出价、ROI或启停变更须分别报告，不能混为单次素材变更')
        normalized={}
        if action=='refresh_creatives' and 'material_ids' in fields:
            items=fields['material_ids']
            if not isinstance(items,list) or len(items)>1000 or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',x) for x in items) or len(set(items))!=len(items):
                raise ValueError('素材补充报告需要不重复的明确material_ids')
            normalized['material_ids']=list(items)
        for name in ("budget","bid","target_roi","running"):
            if name not in fields:continue
            item=fields[name]
            if name=="running":
                if type(item) is not bool:raise ValueError("running必须为明确布尔值")
            else:
                item=numeric(item)
                if item is None:raise ValueError("报告的已观察数值不能留空")
                if name=="budget" and Decimal(str(item)).quantize(Decimal('.01'))!=Decimal(str(item)):
                    raise ValueError("报告预算最多两位小数")
            normalized[name]=item
        value[stage]=normalized
    required="budget" if action in {"budget_up","budget_down","control_budget_up"} else "bid" if action in {"bid_down","bid_restore"} else "target_roi" if action in {"roi_target_up","roi_target_down"} else "running" if action in {"pause_plan","resume_plan","pause_control","pause_creative"} else None
    if status in {"reported_applied","simulated"} and required and any(required not in value[s] for s in ("before","after")):
        raise ValueError("缺少动作对应的前后观察字段")
    if action=="create_chase" and status in {"reported_applied","simulated"} and "budget" not in value["after"]:
        raise ValueError("追投创建报告缺少创建后预算，不能把不存在的旧任务预算填成零")
    if action=='refresh_creatives' and status in {'reported_applied','simulated'}:
        if value['scope']!='plan' or any('material_ids' not in value[s] for s in ('before','after')):
            raise ValueError('补充素材报告必须关联计划并列出前后完整素材ID')
        old,new=(set(value[s]['material_ids']) for s in ('before','after'))
        if not old<new:raise ValueError('补充素材报告必须保留原集合并增加至少一个新素材')
    duration=raw.get("duration_minutes")
    if duration is not None:
        duration=numeric(duration)
        if duration is None or not 0<duration<=1440:raise ValueError("报告持续时长需在0到1440分钟内")
        value["duration_minutes"]=duration
    note=raw.get("note","")
    if not isinstance(note,str) or len(note)>500:raise ValueError("报告备注最多500字")
    value["note"]=note
    return value
