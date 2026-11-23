"""Descriptive monitoring, with session/day and cohort boundaries kept explicit.

Calculations here never certify action eligibility or change a plan lifecycle.
Missing timestamps, incompatible cohorts and zero denominators remain missing.
"""
from collections import defaultdict
from datetime import timedelta

from .engine import _amount_fields, _number, _record, _settings, _time
from .schema import now_cn
from .traffic_quality import observe_quality
from .creative_share import observe_creative_share

STAGES = {
    "live": [("funnel_exposures","曝光"),("funnel_entries","观看"),("funnel_product_clicks","商品点击"),("funnel_created_orders","下单"),("funnel_payments","支付")],
    "product": [("funnel_exposures","曝光"),("funnel_clicks","点击"),("funnel_entries","进入商品页"),("funnel_created_orders","下单"),("funnel_payments","支付")],
    "canonical": [("funnel_exposures","曝光"),("funnel_clicks","点击"),("funnel_entries","进入"),("funnel_created_orders","下单"),("funnel_payments","支付")],
}
OBSERVATION_NOTES = {
    "非日累计统计周期，仅作独立观察，不参与日级小时调控", "统计日期与采集日期不一致",
    "仅采集可见汇总，未覆盖分页计划、素材、退款及库存全量数据",
    "仅当前渲染计划行，未翻页且未核验全量及操作历史",
    "基础与总体台账分别保存，未核验范围前不通过相减推算调控台账",
    "直播大屏为本场累计，不能当作日累计或直接拼接账户小时序列",
}


def _context(record):
    value=record["raw"].get("context",{})
    return value if isinstance(value,dict) else {}


def _valid_observation(record):
    src=record["source"];dt=record["data"]
    if any(issue in record["issues"] for issue in ("含非有限、负数或非数字指标，不能用于调控","页面数据时间在未来")):return False
    if dt is None or dt>record["captured"]+timedelta(minutes=2):return False
    period=src.get("metric_period","day")
    if period=="day":
        if dt.date()!=record["date"]:return False
    elif period=="live_session":
        start=_time(src.get("period_start"))
        if start is None or start>dt or start.date()!=record["date"]:return False
    else:return False
    # Exact collector range warnings do not corrupt a single object's counters.
    # They remain visible and never relax the separate action eligibility gates.
    quality=record["raw"].get("quality",{})
    raw_issues=quality.get("issues",[]) if isinstance(quality,dict) else ["质量信息无效"]
    if not isinstance(raw_issues,list):return False
    return not any(not isinstance(x,str) or x not in OBSERVATION_NOTES for x in raw_issues)


def _financial_fields(record):
    if record["raw"].get("scope")=="creative" and any(record["metrics"].get(k) is not None for k in ("base_spend","base_gmv","base_net_gmv","base_comprehensive_cost","base_orders")):
        return _amount_fields({**record,"raw":{**record["raw"],"scope":"plan"}})
    return _amount_fields(record)


def _observation_status(records,last,rules,now):
    """Current describes the latest capture, not the greatest old data clock."""
    dt=last['data']
    if not _valid_observation(last) or dt is None:
        return 'unverified','最新采集缺少有效指标时间、统计边界或质量证据'
    latest_capture=max(r['captured'] for r in records)
    clocks={r['data'] for r in records if r['captured']==latest_capture}
    if last['captured']!=latest_capture or len(clocks)!=1:
        return 'unverified','最新采集的指标时刻缺失或存在同次矛盾'
    if any(r['data'] is not None and r['captured']<latest_capture and r['data']>dt for r in records):
        return 'unverified','最新采集的指标时间回退，不能沿用较旧有效记录'
    if last['source'].get('metric_period','day')=='day' and last['date']!=now.date():
        return ('stale','日累计统计日期不是今天，仅保留历史观察') if last['date']<now.date() else ('unverified','日累计统计日期在未来')
    data_age=(now-dt).total_seconds();capture_age=(now-last['captured']).total_seconds()
    if min(data_age,capture_age)<-120:return 'unverified','采集或指标时间超出未来容差'
    if max(data_age,capture_age)>rules['max_age_seconds']:return 'stale','采集或指标更新时间已过期'
    return 'fresh','最新采集及指标时间有效，覆盖范围仍需独立核验'


def interval(records, end, fields, rules, hours=1, counters=True):
    """Complete sampled interval within one already-isolated source series."""
    rows=sorted((r for r in records if r["data"]),key=lambda r:(r["data"],r["captured"]))
    result={"complete":False,"start":(end-timedelta(hours=hours)).isoformat(),"end":end.isoformat(),"delta":{},"reason":"缺少完整时间边界"}
    if not rows:return result
    left=min(rows,key=lambda r:abs(r["data"]-(end-timedelta(hours=hours))))
    right=min(rows,key=lambda r:abs(r["data"]-end))
    if abs(left["data"]-(end-timedelta(hours=hours)))>timedelta(minutes=5) or abs(right["data"]-end)>timedelta(minutes=5):return result
    span=(right["data"]-left["data"]).total_seconds()
    if abs(span-hours*3600)>300:return result
    selected=[r for r in rows if left["data"]<=r["data"]<=right["data"]]
    if any(not _valid_observation(r) for r in selected):
        result["reason"]="窗口存在时间、统计周期或数据质量问题";return result
    seen={}
    for row in selected:
        if row["data"] in seen and any(row["metrics"].get(k)!=seen[row["data"]]["metrics"].get(k) for k in fields):
            result["reason"]="同一数据时间存在冲突值";return result
        seen[row["data"]]=row
    selected=sorted(seen.values(),key=lambda r:r["data"])
    if any((b["data"]-a["data"]).total_seconds()>rules["max_gap_minutes"]*60 for a,b in zip(selected,selected[1:])):
        result["reason"]="采集断档";return result
    if any(r["metrics"].get(k) is None for r in selected for k in fields):
        result["reason"]="缺少同口径指标";return result
    if counters and any(b["metrics"][k]+1e-8<a["metrics"][k] for a,b in zip(selected,selected[1:]) for k in fields):
        result["reason"]="累计计数回退或重置";return result
    result.update(complete=True,reason="",observed_start=left['data'].isoformat(),observed_end=right['data'].isoformat(),
                  observed_seconds=span,delta={k:right["metrics"][k]-left["metrics"][k] for k in fields},
                  before={k:left["metrics"][k] for k in fields},after={k:right["metrics"][k] for k in fields})
    return result


def _spend_trend(records,last,rules,now):
    """Observe cost alone; changing net revenue cannot invalidate cost counters.

    ROI and action gates retain their own joint-ledger validation. A decrease
    in net amounts is not sufficient evidence of a refund or attribution time.
    """
    cost,_=_financial_fields(last)
    result={'field':cost,'ledger':'base' if cost.startswith('base_') else 'control' if cost=='control_spend' else 'overall',
            'windows':[],'change_percent':None,'comparison_reason':'需要最近两个完整消耗窗口',
            'is_current':False,'observation_only':True,
            'note':'仅按同源成本累计计算消耗趋势；净额变化原因及归属时段未核验，不据此推算小时ROI或授予调控资格。'}
    dt=last['data']
    if not dt or last['basis']=='unknown':return result
    result['is_current']=_observation_status(records,last,rules,now)[0]=='fresh'
    first=min((r['data'] for r in records if r['data']),default=dt)
    for offset in reversed(range(24)):
        end=dt-timedelta(hours=offset)
        if end<=first:continue
        w=interval(records,end,[cost],rules)
        if w['complete']:
            amount=w['delta'][cost]
            w.update(cost=amount,average_cost_per_hour=amount*3600/w['observed_seconds'])
        result['windows'].append(w)
    recent=result['windows'][-2:]
    if len(recent)==2 and all(w['complete'] for w in recent):
        before,after=(w['average_cost_per_hour'] for w in recent)
        if before>0:
            result.update(change_percent=(after/before-1)*100,comparison_reason='')
        else:result['comparison_reason']='前窗消耗为零，不计算相对增速'
    return result


def _funnel(last):
    ctx=_context(last);m=last["metrics"]
    layout=ctx.get("funnel_layout")
    if not isinstance(layout,str) or layout not in STAGES:layout="live" if last["raw"]["scope"]=="live" else "canonical"
    stages=[{"field":field,"label":label,"value":m.get(field)} for field,label in STAGES[layout]]
    rates=[]
    for a,b in zip(stages,stages[1:]):
        reason=""
        if ctx.get("funnel_same_cohort_verified") is not True:reason="同群体、单位和归因范围尚未核验"
        elif a["value"] is None or b["value"] is None:reason="阶段缺失，不跨过缺失阶段拼接"
        elif a["value"]==0:reason="分母为零"
        elif b["value"]>a["value"]:reason="下游数量超过上游，不满足嵌套漏斗条件"
        rate=None if reason else b["value"]/a["value"]*100
        rates.append({"from":a["field"],"to":b["field"],"from_label":a["label"],"to_label":b["label"],
                      "percent":rate,"dropoff_percent":None if rate is None else 100-rate,"reason":reason})
    comparable=[r for r in rates if r["percent"] is not None]
    return {"layout":layout,"stages":stages,"rates":rates,
            "additional_observations":[{"field":"funnel_orders","label":"成交订单数（未映射为下单或支付）","value":m["funnel_orders"]}] if m.get("funnel_orders") is not None else [],
            "largest_observed_dropoff":max(comparable,key=lambda r:r["dropoff_percent"]) if comparable else None,
            "note":"阶段比率为已声明同群体的描述性统计，不能证明流失原因。"}


def _lifecycle(last, hourly):
    declared=last["control"].get("lifecycle","unknown")
    created=_time(_context(last).get("created_at"));as_of=last["data"] or last["captured"]
    age=(as_of-created).total_seconds()/86400 if created and created<=as_of else None
    candidate="unknown";evidence={"age_days":age}
    if age is not None and age<=3:candidate="cold"
    else:
        ws=hourly[-3:]
        if len(ws)==3 and all(w["complete"] and w.get("sample_sufficient") and w.get("roi") is not None for w in ws):
            spends=[w["spend"] for w in ws];rois=[w["roi"] for w in ws]
            evidence.update(hourly_spend=spends,hourly_roi=rois)
            if spends[0]>spends[1]>spends[2] and rois[0]>rois[1]>rois[2]:candidate="declining_candidate"
            elif spends[0]<spends[1]<spends[2] and rois[-1]>=rois[0] and _context(last).get("model_stable") is True:candidate="growing_candidate"
            elif min(spends)>0 and max(spends)/min(spends)<=1.2 and min(rois)>0 and max(rois)/min(rois)<=1.2 and _context(last).get("model_stable") is True:candidate="mature_candidate"
    return {"declared":declared,"candidate":candidate,"evidence":evidence,"used_for_action":False,
            "rule_basis":"user_strategy_and_local_observation","note":"0—3天为用户冷启动策略；其余为趋势候选，20%稳定带为本地观察阈值，不写回生命周期标签。"}


def analyze_monitoring(snapshots,settings,now=None):
    now=now or now_cn();rules,accounts=_settings(settings);groups=defaultdict(list)
    for raw in snapshots if isinstance(snapshots,list) else []:
        try:row=_record(raw,accounts,now)
        except (TypeError,ValueError,AttributeError,OverflowError):row=None
        if row:
            src=row["source"]
            if not isinstance(src.get("metric_period","day"),str) or src.get("metric_period","day") not in {"day","live_session","unknown"}:continue
            if src.get("period_start") is not None and not isinstance(src["period_start"],str):continue
            groups[row["key"]+(src.get("metric_period","day"),src.get("period_start"))].append(row)
    parent_groups=defaultdict(list)
    for records in groups.values():
        last=max(records,key=lambda r:r['captured'])
        if last['raw']['scope']=='plan':
            plan_id=_context(last).get('plan_id')
            plan_id=plan_id if isinstance(plan_id,str) and plan_id else last['raw']['entity_id']
            parent_groups[(last['raw']['account_id'],plan_id,last['source'].get('series_key'))].append(records)
    entities=[];alerts=[];recommendations=[]
    for key,records in groups.items():
        records.sort(key=lambda r:r['captured'])
        last=records[-1];raw=last["raw"];src=last["source"];m=last["metrics"];ctx=_context(last)
        dt=last['data'];status,status_reason=_observation_status(records,last,rules,now)
        item={"account_id":raw["account_id"],"entity_id":raw["entity_id"],"scope":raw["scope"],"snapshot_id":raw.get("id"),
              "source_key":src.get("series_key"),"metric_basis":last["basis"],"metric_period":src.get("metric_period","day"),
              "data_status":status,"data_status_reason":status_reason,"data_at":src.get("data_at"),"captured_at":src.get("captured_at"),"hourly":[],"warnings":list(last["issues"])}
        item['spend_trend']=_spend_trend(records,last,rules,now)
        if raw['scope']=='creative':
            item['creative_spend_share']=observe_creative_share(records,parent_groups,rules,now,_observation_status)
        if raw["scope"] in {"account","plan"}:
            base=(raw["scope"]=="plan" and (any(m.get(k) is not None for k in ("base_spend","base_gmv","base_net_gmv","base_comprehensive_cost","base_orders")) or (m.get("control_spend") or 0)>0))
            spend,budget=m.get("base_spend" if base else "spend"),m.get("budget")
            period_matches=src.get("metric_period","day")=="day"
            item["budget"]={"spend":spend,"budget":budget,"ledger":"base" if base else "overall",
                            "progress_percent":spend/budget*100 if period_matches and spend is not None and budget is not None and budget>0 else None,
                            "reason":"非日累计不能代替日预算进度" if not period_matches else "缺少同口径预算或消耗" if spend is None or budget is None or budget<=0 else ""}
        if dt:
            cost,revenue=_financial_fields(last)
            if revenue:
                for offset in reversed(range(24)):
                    end=dt-timedelta(hours=offset)
                    if end<=min((r["data"] for r in records if r["data"]),default=dt):continue
                    w=interval(records,end,[cost,revenue],rules)
                    if not w["complete"]:
                        item["hourly"].append(w);continue
                    d=w["delta"];c=d[cost]
                    orders_key="base_orders" if cost.startswith("base_") else "orders"
                    ow=interval(records,end,[orders_key],rules)
                    orders=ow["delta"].get(orders_key) if ow["complete"] else None
                    w.update(spend=c,revenue=d[revenue],roi=d[revenue]/c if c>0 else None,orders=orders,
                             ledger="base" if cost.startswith("base_") else "control" if cost=="control_spend" else "overall",
                             sample_sufficient=c>=rules["min_spend"] and orders is not None and orders>=rules["min_orders"])
                    item["hourly"].append(w)
        if raw["scope"] in {"plan","creative"}:item["lifecycle"]=_lifecycle(last,item["hourly"])
        if raw["scope"] in {"live","product"}:
            item["funnel"]=_funnel(last)
        if raw["scope"]=="live":
            traffic={"direction":"unknown","reason":"需要同一场次至少两个完整小时及在线人数边界","hourly":[]}
            if dt:
                for offset in (1,0):
                    end=dt-timedelta(hours=offset);w=interval(records,end,["spend"],rules)
                    online=interval(records,end,["online_users"],rules,counters=False)
                    if w["complete"] and online["complete"]:
                        traffic["hourly"].append({"end":end.isoformat(),"spend":w["delta"]["spend"],"online_before":online["before"]["online_users"],"online_after":online["after"]["online_users"]})
            if len(traffic["hourly"])==2:
                a,b=traffic["hourly"]
                traffic["direction"]="rising" if b["spend"]>a["spend"] and b["online_after"]>a["online_after"] else "falling" if b["spend"]<a["spend"] and b["online_after"]<a["online_after"] else "stable" if b["spend"]==a["spend"] and b["online_after"]==a["online_after"] else "mixed"
                traffic["reason"]="同场次小时消耗与在线边界的描述性比较"
            item["traffic"]={**traffic,"paid_share":m.get("paid_share"),"natural_share":m.get("natural_share"),"watch_duration":m.get("watch_duration"),"interaction_rate":m.get("interaction_rate"),"order_rate":m.get("order_rate")}
            quality,quality_alerts,quality_recs=observe_quality(last,records,status)
            item['traffic']['quality']=quality
            alerts.extend(quality_alerts);recommendations.extend(quality_recs)
        if raw["scope"]=="product":
            target=_number(accounts[raw["account_id"]].get("target_roi"));compatible=accounts[raw["account_id"]].get("roi_basis")==last["basis"]
            cost,revenue=_amount_fields(last);c=m.get(cost);r=m.get(revenue)
            roi=r/c if c is not None and c>0 and r is not None else None
            verified=ctx.get("product_metrics_verified") is True
            test_state="unverified" if not verified else "target_missing" if not target or not compatible else "insufficient" if c is None or c<rules["min_spend"] or m.get("orders") is None or m["orders"]<rules["min_orders"] else "sample_met_roi_qualified" if roi is not None and roi>=target else "sample_met_roi_low"
            item["product"]={"role":ctx.get("product_role","unknown"),"observed_roi":roi,"stock":m.get("stock"),"new_product_test":test_state if ctx.get("product_role")=="new" else "not_new_product",
                             "account_gmv_contribution_percent":None,"stock_coverage_hours":None,"note":"商品角色由明确标签提供，不从GMV推断利润。样本达标不等于可执行加预算。"}
            link=ctx.get("account_source_key")
            candidates=[max(rs,key=lambda q:q["captured"]) for k,rs in groups.items() if k[0]==raw["account_id"] and k[2]=="account" and k[5]==last["basis"] and rs[-1]["source"].get("series_key")==link]
            candidates=[a for a in candidates if dt and a["data"] and abs((dt-a["data"]).total_seconds())<=300 and a["source"].get("metric_period","day")==src.get("metric_period","day") and a["date"]==last["date"] and _valid_observation(a)]
            if src.get("metric_period","day")=="live_session":
                candidates=[a for a in candidates if _time(src.get("period_start")) is not None and _time(a["source"].get("period_start"))==_time(src["period_start"])]
            if ctx.get("product_account_coverage_verified") is True and len(candidates)==1 and verified and r is not None:
                _,ar=_amount_fields(candidates[0]);total=candidates[0]["metrics"].get(ar)
                if total is not None and total>0 and r<=total:item["product"]["account_gmv_contribution_percent"]=r/total*100
            # Orders are not item quantities. Raw stock and order counts cannot
            # establish hours of sellable inventory; the carrying review uses
            # independently declared available items and paid item quantities.
            if status=="fresh" and m.get("stock")==0:
                alerts.append({"id":"stock_"+str(raw.get("id")),"priority":"P1","account_id":raw["account_id"],"entity_id":raw["entity_id"],"scope":"product","source_basis":last["basis"],"rule":"product_stock_zero","title":"商品来源显示库存为零，需核实承接能力","evidence":{"stock":0,"data_at":src.get("data_at")},"risk":"库存口径及履约状态需人工确认；不自动启停计划","rule_basis":"user_strategy"})
        if raw["scope"] in {"account","live","product"}:
            item["refund"]={"rate_1h":m.get("refund_rate_1h"),"rate_pre_ship":m.get("refund_rate_pre_ship"),"pre_ship_change_pp":None}
            if dt and m.get("refund_rate_pre_ship") is not None:
                w=interval(records,dt,["refund_rate_pre_ship"],rules,counters=False)
                if w["complete"]:
                    change=w["delta"]["refund_rate_pre_ship"];item["refund"]["pre_ship_change_pp"]=change
                    if status=="fresh" and change>=rules["refund_spike_pp"]:
                        alerts.append({"id":"refund_pre_"+str(raw.get("id")),"priority":"P1","account_id":raw["account_id"],"entity_id":raw["entity_id"],"scope":raw["scope"],"source_basis":last["basis"],"rule":"pre_ship_refund_spike","title":"发货前退款率一小时内上升达到策略阈值","evidence":{"change_pp":change,"threshold_pp":rules["refund_spike_pp"],"data_at":src.get("data_at")},"risk":"先人工暂停新增放量并核查商品履约；未证明广告导致退款","rule_basis":"user_strategy"})
        entities.append(item)
    return {"entities":entities,"alerts":alerts,"recommendations":recommendations,"execution_enabled":False,
            "notes":["历史、本场与日累计分别分析，描述性趋势不能自动授予调控资格。","没有同群体证据的漏斗仅展示原始阶段值。","生命周期候选不覆盖人工标签，不推断行业均值或商品利润。"]}
