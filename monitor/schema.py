"""Strict ingestion boundary; never import browser cookies, tokens or raw HTML."""
from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from .official_fields import FIELDS as OFFICIAL_FIELDS,ENDPOINT as OFFICIAL_ENDPOINT
from .observation_time import review_observation_time, DETAIL_PATH, DETAIL_SCOPES
from .plan_operation_observation import plan_operations_from_snapshot

CN = timezone(timedelta(hours=8), "Asia/Shanghai")
SCOPES = {"account", "plan", "creative", "live", "control", "product", "keyword"}
BASES = {"overall_payment", "net", "settled_7d", "marketing", "control_payment", "unknown"}
METRICS = {
    "spend", "gmv", "roi", "net_gmv", "net_roi", "marketing_roi", "settled_roi",
    "comprehensive_cost", "settled_gmv", "budget", "balance", "orders", "target_roi",
    "ctr", "cvr", "cpm", "impressions", "clicks", "refund_rate_1h", "conversion_cost",
    "target_cost", "expected_day_spend", "control_spend", "control_gmv", "base_spend", "base_gmv",
    "base_orders", "base_net_gmv", "base_comprehensive_cost",
    "online_users", "viewers", "gpm", "ecpm", "rejected_creatives", "active_creatives",
    "completion_3s", "completion_rate", "stock", "paid_share", "natural_share"
}
METRICS |= {
    "payment_orders", "net_orders", "payment_conversion_cost", "deep_conversion_cost", "deep_conversion_rate", "account_score",
    "experience_score", "experience_previous_score", "experience_threshold", "reputation_score",
    "refund_rate_pre_ship", "paid_viewers", "natural_viewers", "watch_duration", "interaction_rate",
    "order_rate", "product_clicks", "product_orders", "keyword_rank", "keyword_relevance", "search_roi",
    "smart_share", "similarity_share", "duplicate_count", "active_heated_creatives", "model_age_hours",
    "hourly_gmv", "budget_hit_count", "bid", "previous_bid", "bid_before_drop", "account_roi",
    "min_estimate_convert", "estimate_convert", "estimate_roi_goal", "min_estimate_roi_goal",
    "live_gpm", "watch_to_payment_rate", "funnel_exposures", "funnel_entries", "funnel_product_clicks", "funnel_orders", "funnel_payments",
    "base_roi", "base_net_roi", "base_marketing_roi",
    "account_ctr", "account_cvr", "parent_budget", "control_share_pct", "base_clicks", "base_impressions"
}
METRICS |= {"search_spend", "search_gmv", "search_net_gmv", "search_comprehensive_cost", "search_orders", "search_clicks", "search_impressions",
            "smart_spend", "smart_gmv", "smart_net_gmv", "smart_comprehensive_cost", "smart_orders", "smart_clicks", "smart_impressions",
            "industry_ctr", "funnel_clicks", "funnel_created_orders", "product_impressions", "baseline_watch_duration", "baseline_interaction_rate", "baseline_order_rate"}
METRICS |= {"parent_chase_budget_total"}
METRICS |= {"account_score_threshold", "reputation_threshold"}
METRICS |= {"quality_sample_users", "baseline_quality_sample_users"}
METRICS |= {'available_stock','product_units_sold'}
METRICS |= {'net_roi_goal','payment_roi_goal'}
METRICS |= {'net_conversion_cost','net_settlement_rate'}
METRICS |= {value[0] for value in OFFICIAL_FIELDS.values()}
PERCENT_METRICS = {
    "ctr", "cvr", "refund_rate_1h", "completion_rate", "completion_3s", "deep_conversion_rate",
    "refund_rate_pre_ship", "paid_share", "natural_share", "interaction_rate", "order_rate", "smart_share",
    "similarity_share", "control_share_pct", "account_ctr", "account_cvr", "keyword_relevance", "watch_to_payment_rate"
}
PERCENT_METRICS |= {"industry_ctr", "baseline_interaction_rate", "baseline_order_rate"}
PERCENT_METRICS |= {'net_settlement_rate'}
COUNT_METRICS={"orders","payment_orders","net_orders","base_orders","impressions","clicks","base_clicks","base_impressions",
               "online_users","viewers","rejected_creatives","active_creatives","stock","paid_viewers","natural_viewers",
               "product_clicks","product_orders","keyword_rank","duplicate_count","active_heated_creatives","budget_hit_count",
               "funnel_exposures","funnel_entries","funnel_product_clicks","funnel_orders","funnel_payments",
               "min_estimate_convert","estimate_convert"}
COUNT_METRICS |= {"search_orders","search_clicks","search_impressions","smart_orders","smart_clicks","smart_impressions","funnel_clicks","funnel_created_orders","product_impressions"}
COUNT_METRICS |= {"quality_sample_users", "baseline_quality_sample_users"}
COUNT_METRICS |= {'available_stock','product_units_sold'}
COUNT_METRICS |= {value[0] for value in OFFICIAL_FIELDS.values() if value[2]=='单'}
CONTEXT_IDS = {"plan_id", "parent_plan_id", "aweme_id", "material_id", "product_id", "keyword_id", "industry_id", "baseline_industry_id", "duplicate_group_id", "causal_review_control_id", "last_chase_id", "previous_chase_id", "last_pause_control_id"}
CONTEXT_TIMES = {"created_at", "last_pause_at", "last_chase_at", "last_bid_change_at", "account_metrics_at", "predicted_overload_at", "industry_baseline_at", "control_started_at", "causal_review_at"}
CONTEXT_FLAGS = {
    "model_stable", "compliance_ok", "anchor_ready", "promotion_peak", "carrying_ready", "manual_reviewed",
    "bid_change_history_known", "chase_history_known", "account_metrics_verified", "account_blocked",
    "permission_restricted", "violation_flag", "differentiated_targeting_verified"
}
CONTEXT_FLAGS |= {"search_ledger_verified", "smart_ledger_verified", "industry_baseline_verified", "is_primary_plan", "creative_replacement_failed",
                  "funnel_same_cohort_verified", "product_metrics_verified", "product_account_coverage_verified", "quality_baseline_verified", "smart_enabled", "duplicate_group_verified"}
CONTEXT_FLAGS |= {"crowding_causality_verified", "account_roi_causality_verified", "parent_control_inventory_verified", "latest_budget_verified", "bid_maintenance_reviewed"}
CONTEXT_FLAGS |= {"is_core_creative", "low_latency_verified", "live_ongoing", "maintenance_active", "score_thresholds_verified", "delivery_expected"}
CONTEXT_IDS |= {"quality_definition_id", "baseline_quality_definition_id", "quality_segment_id", "baseline_quality_segment_id"}
CONTEXT_TIMES |= {"quality_window_start", "quality_window_end", "quality_baseline_start", "quality_baseline_end"}
CONTEXT_FLAGS |= {"quality_metrics_verified"}
CONTEXT_IDS |= {"creative_refresh_event_id"}
CONTEXT_TIMES |= {"creative_inventory_at", "decline_started_at", "bid_maintenance_review_at"}
CONTEXT_FLAGS |= {"creative_inventory_verified"}
CONTEXT_TIMES |= {'report_start_at','report_end_at'}
CONTEXT_TIMES |= {'plan_roi_observed_at'}
CONTEXT_FLAGS |= {'plan_roi_config_verified'}
CONTEXT_TIMES |= {'plan_bid_observed_at'}
CONTEXT_FLAGS |= {'plan_bid_config_verified'}
CONTEXT_FLAGS |= {'cold_test_threshold_verified','plan_budget_period_verified'}
CONTEXT_IDS |= {'roi_recovery_event_id'}
CONTEXT_TIMES |= {'plan_status_at'}
CONTEXT_FLAGS |= {'day_boundary_verified'}
CONTEXT_FLAGS |= {'inventory_units_verified','product_sales_units_verified','stock_policy_verified',
                  'product_plan_mapping_verified','explanation_verified','explanation_active'}
CONTEXT_FLAGS |= {'creative_parent_verified'}
CONTEXT_FLAGS |= {'plan_multi_aweme'}
CONTEXT_FLAGS |= {'smart_control_budget_reviewed'}
CONTEXT_TIMES |= {'inventory_at','product_mapping_at','explanation_observed_at','explanation_started_at','explanation_ends_at'}
CONTEXT_IDS |= {'promoted_product_id','explained_product_id','promoted_category_id','explained_category_id','live_room_id'}


def normalize_context(raw, kind):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("context 必须为对象")
    value = {}
    if 'observed_duration_mode' in raw:
        if not isinstance(raw['observed_duration_mode'],str) or raw['observed_duration_mode'] not in {'fixed','unlimited','unknown'}:
            raise ValueError('明细观察时长类型无效')
        value['observed_duration_mode']=raw['observed_duration_mode']
    if 'observed_duration_hours' in raw:
        hours=numeric(raw['observed_duration_hours'])
        if hours is None or hours<=0 or raw.get('observed_duration_mode')!='fixed':
            raise ValueError('固定明细时长须为明确正数')
        value['observed_duration_hours']=hours
    if 'control_delivery_mode' in raw:
        if not isinstance(raw['control_delivery_mode'],str) or raw['control_delivery_mode'] not in {'cost_control','volume','unknown'}:
            raise ValueError('调控明细投放方式无效')
        value['control_delivery_mode']=raw['control_delivery_mode']
    for key,allowed in (('plan_marketing_goal',{'LIVE_PROM_GOODS','VIDEO_PROM_GOODS','unknown'}),
                        ('plan_delivery_mode',{'cost_control','volume','unknown'}),
                        ('plan_optimization_goal',{'AD_CONVERT_TYPE_LIVE_PAY_ROI','AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI','unknown'}),
                        ('plan_delivery_state',{'delivering','system_paused','not_delivering','unknown'})):
        if key in raw:
            if not isinstance(raw[key],str) or raw[key] not in allowed:raise ValueError('计划营销目标、投放模式或状态枚举无效')
            value[key]=raw[key]
    for key in ('plan_roi_step','plan_roi_min','plan_roi_max','cold_min_test_spend','minimum_stock_hours','smart_control_budget'):
        if key in raw:
            value[key]=numeric(raw[key])
            if value[key] is not None and value[key]<=0:raise ValueError('计划策略步长、范围和消耗门槛必须为正数或null')
    if (value.get('plan_roi_min') is not None and value.get('plan_roi_max') is not None
            and value['plan_roi_min']>value['plan_roi_max']):raise ValueError('计划ROI允许范围顺序无效')
    if 'plan_roi_mode' in raw:
        if not isinstance(raw['plan_roi_mode'],str) or raw['plan_roi_mode'] not in {'roi_target','conversion_cost','unknown'}:
            raise ValueError('计划ROI优化模式无效')
        value['plan_roi_mode']=raw['plan_roi_mode']
    if 'plan_budget_period' in raw:
        if not isinstance(raw['plan_budget_period'],str) or raw['plan_budget_period'] not in {'day','total','unknown'}:
            raise ValueError('计划预算期间无效')
        value['plan_budget_period']=raw['plan_budget_period']
    for key,allowed in (('report_endpoint',{OFFICIAL_ENDPOINT}),('report_adlab_scene',{'OVERALL_PROJECT','UNI_PROJECT'}),
                        ('report_data_period',{'ALL_DATA','OVER_ALL_DATA','UNI_DATA'}),
                        ('report_marketing_goal',{'ALL','LIVE_PROM_GOODS','VIDEO_PROM_GOODS'}),
                        ('report_order_platform',{'ALL','QIANCHUAN','ECP_AWEME'})):
        if key in raw:
            if not isinstance(raw[key],str) or raw[key] not in allowed:raise ValueError('官方报告范围枚举无效')
            value[key]=raw[key]
    if 'report_fields' in raw:
        fields=raw['report_fields']
        if not isinstance(fields,list) or not fields or any(not isinstance(f,str) or f not in OFFICIAL_FIELDS for f in fields) or len(set(fields))!=len(fields):
            raise ValueError('官方报告字段列表无效或未核验')
        value['report_fields']=sorted(fields)
    if 'creative_ids' in raw:
        items=raw['creative_ids']
        if not isinstance(items,list) or len(items)>1000 or any(not isinstance(x,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',x) for x in items) or len(set(items))!=len(items):
            raise ValueError('creative_ids需要最多1000个不重复的明确字符串ID')
        value['creative_ids']=list(items)
    for key in CONTEXT_IDS:
        if key in raw and raw[key] is not None:
            if not isinstance(raw[key], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", raw[key]):
                raise ValueError(f"{key} 必须为明确的字符串ID")
            value[key] = raw[key]
    for key in CONTEXT_TIMES:
        if raw.get(key) is not None:
            value[key] = parse_time(raw[key]).isoformat()
    for key in CONTEXT_FLAGS:
        if key in raw:
            if raw[key] is not None and type(raw[key]) is not bool:
                raise ValueError(f"{key} 必须为布尔值或null")
            value[key] = raw[key]
    for key, allowed in (("account_metric_basis", BASES), ("target_metric_basis", BASES), ('plan_roi_basis',BASES), ('target_cost_basis',BASES),
                         ('day_boundary_role',{'closing','opening','unknown'}),
                         ('inventory_scope',{'product','sku','unknown'}),
                         ('product_sales_scope',{'total_product','ad_attributed','unknown'}),
                         ('promotion_product_mode',{'single_product','catalog','unknown'}),
                         ('plan_bid_mode',{'conversion_cost','roi','unknown'}),
                         ('spend_ledger',{'base','overall','unknown'}),
                         ('observed_plan_view',{'1','2','unknown'}),
                         ("creative_type", {"custom", "programmatic", "unknown"}),
                         ("control_type", {"material_chase","one_click_boost","one_click_control","other","unknown"})):
        if key in raw:
            if not isinstance(raw[key],str) or raw[key] not in allowed:
                raise ValueError(f"{key} 枚举无效")
            value[key] = raw[key]
    if "budget_mode" in raw:
        if raw["budget_mode"] not in {"specified", "suggested", "unknown"}:
            raise ValueError("预算类型枚举无效")
        value["budget_mode"]=raw["budget_mode"]
    if 'report_granularity' in raw:
        if not isinstance(raw['report_granularity'],str) or raw['report_granularity'] not in {'minute','hour','day','unknown'}:
            raise ValueError('report_granularity枚举无效')
        value['report_granularity']=raw['report_granularity']
    for key in ("account_source_key","parent_source_key"):
        if raw.get(key) is not None:
            source_key = raw[key]
            if not isinstance(source_key, str) or not 1 <= len(source_key) <= 1000 or any(ord(c)<32 for c in source_key):
                raise ValueError("关联账户/母计划来源键无效")
            value[key] = source_key
    for key,allowed in (("product_role", {"hit","lead","profit","new","unknown"}),
                        ("funnel_layout", {"live","product","canonical","unknown"}),
                        ("content_format", {"image_text","short_video","unknown"})):
        if key in raw:
            if raw[key] not in allowed:raise ValueError(f"{key} 枚举无效")
            value[key]=raw[key]
    for field in ("industry_baseline_source", "quality_baseline_source"):
        if raw.get(field) is not None:
            description=raw[field]
            if not isinstance(description,str) or not 1<=len(description)<=500 or any(ord(c)<32 for c in description):
                raise ValueError("基线来源说明无效")
            value[field]=description
    if kind in {"browser_dom", "legacy_db"}:
        # A DOM observation cannot certify human review or a complete mutation history.
        for key in ("manual_reviewed", "bid_change_history_known", "chase_history_known", "account_metrics_verified", "differentiated_targeting_verified",
                    "search_ledger_verified", "smart_ledger_verified", "industry_baseline_verified", "funnel_same_cohort_verified",
                    "product_metrics_verified", "product_account_coverage_verified", "quality_baseline_verified", "duplicate_group_verified",
                    "crowding_causality_verified", "account_roi_causality_verified", "parent_control_inventory_verified", "latest_budget_verified", "bid_maintenance_reviewed",
                    "low_latency_verified", "maintenance_active", "score_thresholds_verified", "delivery_expected", "quality_metrics_verified", "creative_inventory_verified", "plan_roi_config_verified", 'cold_test_threshold_verified','plan_budget_period_verified','day_boundary_verified',
                    'inventory_units_verified','product_sales_units_verified','stock_policy_verified','product_plan_mapping_verified','explanation_verified','creative_parent_verified','smart_control_budget_reviewed'):
            value[key] = None
    return value


def now_cn():
    return datetime.now(CN)


def parse_time(value):
    if not isinstance(value, str) or len(value) > 50:
        raise ValueError("时间必须为 ISO 8601 字符串")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=CN) if dt.tzinfo is None else dt.astimezone(CN)


def numeric(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("指标必须为数字或 null，不能使用格式化文本")
    if value < 0 or value > 1e15 or not math.isfinite(value):
        raise ValueError("指标必须为有限的非负数字")
    return value


def clean_url(value, account_id):
    if not value:
        return ""
    if not isinstance(value, str) or len(value) > 20000:
        raise ValueError("来源地址无效")
    u = urlsplit(value)
    if u.scheme != "https" or u.hostname != "qianchuan.jinritemai.com" or u.username or u.password:
        raise ValueError("浏览器来源只允许千川官方域名")
    supplied = parse_qs(u.query).get("aavid", [])
    if supplied != [account_id]:
        raise ValueError("来源 URL 的账户 ID 与快照不一致")
    if u.path not in {"/uni-prom", "/uni-prom/detail", "/overall-prom", "/overall-prom/detail", "/board-next"}:
        raise ValueError("来源页面不在本次观察范围内")
    # IDs and tokens in arbitrary query/fragment parameters are not persisted.
    return urlunsplit(("https", u.hostname, u.path, urlencode({"aavid": account_id}), ""))


def normalize_snapshot(raw, settings, received_at=None):
    if not isinstance(raw, dict):
        raise ValueError("快照必须为 JSON 对象")
    account_id = raw.get("account_id")
    accounts = {a["id"]: a for a in settings["accounts"]}
    if not isinstance(account_id, str) or account_id not in accounts:
        raise ValueError("账户不在当前配置白名单内")
    scope = raw.get("scope", "account")
    if scope not in SCOPES:
        raise ValueError("对象层级无效")
    entity_id = raw.get("entity_id", account_id)
    if not isinstance(entity_id, str) or not entity_id or len(entity_id) > 150:
        raise ValueError("对象 ID 必须是非空字符串")
    basis = raw.get("metric_basis", "unknown")
    if basis not in BASES:
        raise ValueError("ROI 口径无效")
    src = raw.get("source", {})
    if not isinstance(src, dict):
        raise ValueError("来源信息无效")
    kind = src.get("kind", "import")
    if kind not in {"browser_dom", "legacy_db", "import", "demo"}:
        raise ValueError("来源类型无效")
    captured = parse_time(src.get("captured_at", ""))
    received = received_at or now_cn()
    if captured > received + timedelta(minutes=2):
        raise ValueError("采集时间在未来，请检查电脑时间")
    data_at = parse_time(src["data_at"]) if src.get("data_at") else None
    if data_at and data_at > captured + timedelta(minutes=2):
        raise ValueError("数据更新时间晚于采集时间")
    metric_date = src.get("metric_date", "")
    try:
        if not isinstance(metric_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", metric_date):
            raise ValueError("invalid date format")
        datetime.strptime(metric_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        raise ValueError("统计日期必须明确填写 YYYY-MM-DD") from None
    url = clean_url(src.get("url", ""), account_id)
    if kind == "browser_dom" and not url:
        raise ValueError("浏览器快照必须包含来源 URL")
    dataset_id = src.get("dataset_id")
    if dataset_id is None:
        # An unidentified import must never join another unidentified report.
        dataset_id = "batch_" + secrets.token_hex(12) if kind == "import" else kind
    if not isinstance(dataset_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", dataset_id):
        raise ValueError("dataset_id 仅允许1到80个字母、数字、下划线或短横线")
    page_path = urlsplit(url).path if url else "report"
    segment = src.get('segment')
    if segment is not None:
        material_segment=(isinstance(segment,str) and segment in {'material_cost_control','material_volume','material_all'}
            and scope=='creative' and page_path=='/board-next' and src.get('metric_period')=='live_session' and src.get('period_start'))
        plan_card_segment=(isinstance(segment,str) and segment in {'plan_card_overall','plan_card_base'}
            and kind=='browser_dom' and scope=='plan' and page_path==DETAIL_PATH and src.get('metric_period','day')=='day')
        plan_log_segment=(segment=='plan_operation_log' and kind=='browser_dom' and scope=='plan'
            and page_path==DETAIL_PATH and src.get('metric_period','day')=='day')
        if not (material_segment or plan_card_segment or plan_log_segment):
            raise ValueError('观察分段须为本场素材筛选，或直播计划详情的明确当日总体/基础卡片')
    parent_entity_id=src.get('parent_entity_id')
    if parent_entity_id is not None and (not isinstance(parent_entity_id,str) or not re.fullmatch(r'[0-9]{6,30}',parent_entity_id)
            or scope not in {'product','control','creative'}):
        raise ValueError('明细来源须使用明确的母计划ID和子对象层级')
    issues = []
    if not data_at:
        issues.append("缺少页面数据更新时间，采集时间不能替代数据时间")
    if metric_date != captured.date().isoformat():
        issues.append("统计日期与采集日期不一致")
    if basis == "unknown":
        issues.append("ROI 口径未确认")
    period = src.get("metric_period", "day")
    if period not in {"day", "live_session", "unknown"}:
        raise ValueError("统计周期无效")
    period_start = parse_time(src["period_start"]).isoformat() if src.get("period_start") else None
    if period != "day":
        issues.append("非日累计统计周期，仅作独立观察，不参与日级小时调控")
    metrics = raw.get("metrics", {})
    if not isinstance(metrics, dict):
        raise ValueError("metrics 必须为对象")
    metrics = {k: numeric(v) for k, v in metrics.items() if k in METRICS}
    if any(v is not None and isinstance(v,float) and not v.is_integer() for k,v in metrics.items() if k in COUNT_METRICS):
        raise ValueError("订单、次数、人数等计数指标必须为整数")
    metrics = {k: int(v) if k in COUNT_METRICS and v is not None else v for k,v in metrics.items()}
    if segment == 'plan_card_base' and set(metrics)-{'base_spend','base_gmv','base_net_gmv','base_comprehensive_cost',
            'base_roi','base_net_roi','base_marketing_roi','base_orders','budget','net_roi_goal','payment_roi_goal'}:
        raise ValueError('基础卡片只能写入base_*及明确配置，不能冒充总体业绩')
    if segment == 'plan_operation_log' and (metrics or basis != 'unknown'):
        raise ValueError('计划日志分段只保存日志，不携带业绩或预算配置指标')
    if not any(v is not None for v in metrics.values()) and segment != 'plan_operation_log':
        raise ValueError("没有可识别的数字指标")
    if any(metrics.get(k, 0) is not None and metrics.get(k, 0) > 100 for k in PERCENT_METRICS):
        raise ValueError("百分比指标请使用 0 到 100 的数值")
    supplied_issues = raw.get("quality", {}).get("issues", []) if isinstance(raw.get("quality", {}), dict) else []
    if isinstance(supplied_issues, list):
        issues += [str(x)[:200] for x in supplied_issues[:15]]
    ctl = raw.get("control", {})
    if not isinstance(ctl, dict):
        raise ValueError("调控历史信息无效")
    # Imported data can describe history, but never enables a real executor.
    control = {"history_known": ctl.get("history_known") is True,
               "guarantee_verified": ctl.get("guarantee_verified") is True and kind in {"import", "demo"},
               "running": ctl.get("running") if type(ctl.get("running")) is bool else None,
               "lifecycle": ctl.get("lifecycle", "unknown")}
    if control["lifecycle"] not in {"cold", "growing", "mature", "declining", "unknown"}:
        control["lifecycle"] = "unknown"
    for key in ("budget_original", "budget_increase_today", "changes_today"):
        control[key] = numeric(ctl.get(key))
    control["last_change_at"] = parse_time(ctl["last_change_at"]).isoformat() if ctl.get("last_change_at") else None
    promotion_type = raw.get("promotion_type") if raw.get("promotion_type") in {"universal", "standard", "search", "chengfang"} else "unknown"
    display_name = raw.get("display_name", entity_id)
    if not isinstance(display_name, str) or len(display_name)>150:
        raise ValueError("对象显示名称无效")
    snapshot = {
        "account_id": account_id, "entity_id": entity_id, "scope": scope,
        "display_name": display_name,
        "scene": accounts[account_id]["scene"],
        "promotion_type": promotion_type,
        "metric_basis": basis,
        "source": {"kind": kind, "url": url, "captured_at": captured.isoformat(),
                   "data_at": data_at.isoformat() if data_at else None, "metric_date": metric_date,
                   "metric_period": period, "period_start": period_start,
                   "dataset_id": dataset_id,
                   "series_key": f"{kind}:{dataset_id}:{page_path}:{scope}:{entity_id}:{basis}:{period}:{period_start or ''}:{promotion_type}",
                   "coverage": "report" if src.get("coverage") == "report" and kind != "browser_dom" else "visible_only"},
        "metrics": metrics, "quality": {"issues": list(dict.fromkeys(issues))}, "control": control,
        "context": normalize_context(raw.get("context"), kind),
    }
    if segment is not None:
        snapshot['source']['segment'] = segment
        snapshot['source']['series_key'] += ':' + segment
    if page_path=='/overall-prom' and snapshot['context'].get('observed_plan_view') in {'1','2'}:
        snapshot['source']['series_key'] += ':plan_view:' + snapshot['context']['observed_plan_view']
    if parent_entity_id is not None:
        if snapshot['context'].get('parent_plan_id')!=parent_entity_id:
            raise ValueError('明细来源母计划与对象关系不一致')
        snapshot['source']['parent_entity_id']=parent_entity_id
        snapshot['source']['series_key'] += ':parent_plan:' + parent_entity_id
    if kind == 'browser_dom' and page_path == DETAIL_PATH and scope in DETAIL_SCOPES and src.get('reported_detail_header_data_at') is not None:
        reported = parse_time(src['reported_detail_header_data_at'])
        if reported > captured + timedelta(minutes=2):
            raise ValueError('详情顶部时间晚于采集时间')
        snapshot['source']['reported_detail_header_data_at'] = reported.isoformat()
    supplied_context = raw.get('context')
    if segment == 'plan_operation_log' or isinstance(supplied_context, dict) and 'observed_plan_operations' in supplied_context:
        if (not isinstance(supplied_context, dict)
                or set(supplied_context) != {'plan_id','observed_plan_operations'}):
            raise ValueError('计划日志须独立提供明确计划ID和可见日志行')
        snapshot['context']['observed_plan_operations'] = supplied_context['observed_plan_operations']
        operations = plan_operations_from_snapshot(snapshot)
        snapshot['context']['observed_plan_operations'] = operations
        latest_change = max(parse_time(row['occurred_at']) for row in operations).isoformat()
        if control['last_change_at'] is not None and control['last_change_at'] != latest_change:
            raise ValueError('最近操作时间须与可见日志的最新记录一致')
        control['last_change_at'] = latest_change
        control['history_known'] = False
        control['running'] = None
        issues.append('仅明确可见的任务预算修改日志，不认证完整历史或平台执行结果')
        snapshot['quality']['issues'] = list(dict.fromkeys(issues + snapshot['quality']['issues']))
    snapshot = review_observation_time(snapshot)
    snapshot["id"] = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
    return snapshot


def validate_settings(value):
    # Strategy settings cannot alter the allowlist; the explicit registry is a separate guarded route.
    if not isinstance(value, dict):
        raise ValueError("设置必须为对象")
    rules = value.get("rules", {})
    limits = {
        "max_age_seconds": (60, 3600), "min_spend": (0, 1000000), "min_orders": (1, 10000),
        "max_gap_minutes": (1, 30), "roi_low_ratio": (.1, 1), "chase_roi_low_ratio": (.1, 1),
        "budget_step_up": (.01, .2), "budget_step_down": (.1, .3), "daily_budget_up_cap": (.01, .5),
        "cooldown_minutes": (60, 1440), "spend_surge_ratio": (1.1, 10),
        "refund_spike_pp": (1, 100), "creative_ctr_decay": (.01, .9), "control_share_cap": (.01, .2),
        "specialized_min_impressions": (100, 1000000), "cold_bid_max_target_ratio": (1.4, 1.4),
        "growth_roi_max_ratio": (1,1.2), "growth_cost_max_ratio": (1,1.2), "growth_spend_max_ratio": (1,1.3)
    }
    if not isinstance(rules, dict) or set(rules) - set(limits):
        raise ValueError("未知策略参数")
    for key, val in rules.items():
        n = numeric(val)
        if n is None or not limits[key][0] <= n <= limits[key][1]:
            raise ValueError(f"{key} 超出允许范围 {limits[key]}")
        if key=="specialized_min_impressions" and n!=int(n):
            raise ValueError("specialized_min_impressions 必须为整数")
    accounts = value.get("accounts", [])
    if not isinstance(accounts, list):
        raise ValueError("账户设置必须为数组")
    for a in accounts:
        if not isinstance(a, dict) or not isinstance(a.get("id"), str):
            raise ValueError("账户 ID 无效")
        if a.get("roi_basis", "unknown") not in BASES:
            raise ValueError("账户 ROI 口径无效")
        n = numeric(a.get("target_roi"))
        if n is not None and not 0 < n <= 10000:
            raise ValueError("目标 ROI 必须大于 0")
    return {"rules": rules, "accounts": accounts}
