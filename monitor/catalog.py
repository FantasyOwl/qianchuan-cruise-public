"""Coverage inventory. Supported input fields are not evidence of live collection."""
from __future__ import annotations

from .schema import METRICS, now_cn, parse_time
from .observation_time import review_observation_time

# Exact labels and the object level are part of a metric's meaning.
GROUPS = [
    ("账户消耗", {"account"}, [("spend","累计消耗","元"),("budget","日预算","元"),("balance","余额","元"),("expected_day_spend","预计当日消耗","元")]),
    ("账户效果", {"account"}, [("roi","整体支付ROI","倍"),("net_roi","净成交ROI","倍"),("settled_roi","7日结算ROI","倍"),("marketing_roi","综合营销ROI","倍"),("conversion_cost","转化成本","元"),("deep_conversion_cost","深度转化成本","元"),("gpm","广告GPM","元"),("ecpm","eCPM","元")]),
    ("流量质量", {"account","plan"}, [("ctr","点击率","%"),("cvr","转化率","%"),("cpm","千次展示成本","元"),("deep_conversion_rate","深度转化率","%")]),
    ("合规与店铺", {"account"}, [("rejected_creatives","审核驳回数","个"),("account_score","账户分值","分"),("experience_score","店铺体验分","分"),("reputation_score","口碑分","分")]),
    ("计划与基础台账", {"plan"}, [("spend","计划累计消耗","元"),("budget","计划预算","元"),("target_roi","平台计划当前ROI目标","倍"),("base_spend","基础消耗","元"),("base_gmv","基础成交金额","元"),("base_net_gmv","基础净成交金额","元"),("base_orders","同口径基础订单数","单"),("natural_share","自然流占比","%"),("budget_hit_count","预算撞线次数","次")]),
    ("搜索", {"keyword","plan"}, [("keyword_rank","关键词排名","位"),("keyword_relevance","搜索相关度","%"),("search_roi","搜索ROI","倍"),("smart_share","智选流量占比","%")]),
    ("创意", {"creative"}, [("completion_3s","3秒完播率","%"),("completion_rate","整体完播率","%"),("ctr","素材点击率","%"),("cvr","素材转化率","%"),("roi","素材支付ROI","倍"),("spend","素材消耗","元"),("active_heated_creatives","同时加热素材数","个"),("similarity_share","同质化素材占比","%"),("duplicate_count","重复素材数","条")]),
    ("调控独立台账", {"control"}, [("control_spend","调控消耗","元"),("control_gmv","调控成交金额","元"),("budget","调控预算","元"),("bid","调控出价","元")]),
    ("直播大屏", {"live"}, [("roi","本场整体支付ROI","倍"),("spend","本场累计消耗","元"),("online_users","实时在线人数","人"),("viewers","整体观看人数","人"),("live_gpm","直播间GPM","元"),("watch_duration","平均停留时长","秒"),("interaction_rate","互动率","%"),("refund_rate_1h","1小时退款率","%"),("refund_rate_pre_ship","发货前退款率","%")]),
    ("转化漏斗", {"live","product"}, [("funnel_exposures","整体曝光次数","次"),("funnel_clicks","曝光后点击次数","次"),("funnel_entries","观看或进入次数","次"),("funnel_product_clicks","商品点击次数","次"),("funnel_orders","成交订单数（阶段未确认）","单"),("funnel_created_orders","创建/下单订单数","单"),("funnel_payments","支付订单数","单")]),
    ("商品", {"product"}, [("product_clicks","商品点击次数","次"),("product_orders","商品订单数","单"),("gmv","商品成交金额","元"),("roi","商品支付ROI","倍"),("stock","原始库存","件"),("available_stock","已声明可售件数","件"),("product_units_sold","商品支付件数累计","件")]),
]


def coverage_report(snapshots, settings, now=None, *, analysis=None):
    snapshots = [review_observation_time(row) for row in snapshots]
    now=now or now_cn()
    if analysis is None:
        from .analytics import analyze_monitoring
        analysis=analyze_monitoring(snapshots,settings,now) if any(s.get('scope')=='creative' for s in snapshots) else {}
    # Keep sources separate; never let one object stand in for another account.
    latest={}
    for s in snapshots:
        if not isinstance(s,dict) or s.get("source",{}).get("kind")=="demo":continue
        src=s.get("source",{})
        key=(s.get("account_id"),s.get("entity_id"),s.get("scope"),s.get("metric_basis"),src.get("series_key"))
        if key not in latest or src.get("captured_at","")>latest[key].get("source",{}).get("captured_at",""):
            latest[key]=s
    accounts=[]
    for account in settings.get("accounts",[]):
        rows=[]
        records=[s for s in latest.values() if s.get("account_id")==account["id"]]
        for group,scopes,fields in GROUPS:
            for field,label,unit in fields:
                present=[s for s in records if s.get("scope") in scopes and s.get("metrics",{}).get(field) is not None
                         and (field!="roi" or s.get("metric_basis")=="overall_payment")]
                evidence=[]
                for s in present:
                    src=s["source"]
                    status="unverified"
                    try:
                        dt=parse_time(src.get("data_at"))
                        if dt>now:
                            status="unverified"
                        elif (now-dt).total_seconds()>settings.get("rules",{}).get("max_age_seconds",600) or src.get("metric_date")!=now.date().isoformat():status="stale"
                        elif src.get("metric_period","day")=="day" and not s.get("quality",{}).get("issues"):status="fresh"
                    except (ValueError,TypeError):pass
                    evidence.append({"snapshot_id":s.get("id"),"entity_id":s.get("entity_id"),"scope":s.get("scope"),
                                     "metric_basis":s.get("metric_basis"),"value":s["metrics"][field],"unit":unit,
                                     "source_kind":src.get("kind"),"data_at":src.get("data_at"),"metric_date":src.get("metric_date"),"status":status})
                states={e["status"] for e in evidence}
                status="fresh" if "fresh" in states else "unverified" if "unverified" in states else "stale" if states else "missing"
                rows.append({"id":group+":"+field,"group":group,"field":field,"label":label,"unit":unit,
                             "supported_input":field in METRICS,"data_status":status,"objects_observed":len(evidence),"evidence":evidence})
        derived=[]
        for item in analysis.get('entities',[]):
            value=item.get('creative_spend_share')
            if item.get('account_id')!=account['id'] or not value:continue
            state='fresh' if value['is_current'] else 'stale' if value['status']=='historical' else 'unverified'
            derived.append({'snapshot_id':item['snapshot_id'],'entity_id':item['entity_id'],'scope':'creative',
                            'metric_basis':item['metric_basis'],'value':value['share_percent'],'unit':'%',
                            'source_kind':'local_linked_calculation','data_at':value['creative_data_at'],'status':state,
                            'reason':value['reason'],'parent_snapshot_id':value['parent_snapshot_id']})
        states={r['status'] for r in derived}
        state='fresh' if 'fresh' in states else 'unverified' if 'unverified' in states else 'stale' if states else 'missing'
        rows.append({'id':'创意:creative_spend_share','group':'创意','field':'creative_spend_share','label':'单素材消耗占比','unit':'%',
                     'supported_input':False,'derived':True,'data_status':state,'objects_observed':len(derived),'evidence':derived})
        counts={state:sum(r["data_status"]==state for r in rows) for state in ("fresh","unverified","stale","missing")}
        accounts.append({"account_id":account["id"],"name":account.get("name"),"counts":counts,"metrics":rows})
    return {"accounts":accounts,"checked_at":now.isoformat(),"scope":"observed_source_inventory",
            "notes":["支持报告字段不代表已完成实时采集。","未采到的指标保持缺失，不填0。","本场累计、日累计和不同ROI口径不相互替代。","单个可见计划不代表全量计划、创意或商品覆盖。"]}
