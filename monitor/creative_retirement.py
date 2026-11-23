"""Fresh local retirement review after an add; supplied facts never authorize IO."""
from .creative_rotation import _registry,_bound_record,_asset_key,_clock,WEAK_RULES,WEAK_ACTIONS
from .engine import _record,_settings,_time


def review_retirement(draft,payload,snapshots,settings,evaluation,now):
    rules,accounts=_settings(settings);plans,profiles=_registry(payload,settings)
    account,pid=draft['account_id'],draft['plan_id'];selected=draft['selection'];block=[]
    plan=next((p for p in plans if p['account_id']==account and p['plan_id']==pid),None)
    out={'local_eligible':False,'platform_verified':False,'executed':False,'blockers':block,
         'checked_at':now.isoformat(),'request':None}
    if plan is None:block.append('新清单没有对应母计划');return out
    index={'by_id':{},'by_key':{}}
    for raw in snapshots:
        r=_record(raw,accounts,now)
        if r is not None:
            index['by_id'].setdefault(raw.get('id'),[]).append(r)
            index['by_key'].setdefault(r['key'],[]).append(r)
    parent,errors=_bound_record(plan['parent_snapshot_id'],account,'plan',index,evaluation,rules,accounts,now);block.extend(errors)
    if not parent or parent['raw']['entity_id']!=pid or parent['raw']['scene']!=draft['scene'] or parent['raw']['promotion_type']!='universal':
        block.append('母计划身份、场景或全域类型不一致')
    if parent and (not parent['data'] or abs((_clock(plan['observed_at'])-parent['data']).total_seconds())>1):
        block.append('配置清单与母计划数据截点不一致')
    if not all(plan[k] for k in ('inventory_verified','inventory_complete','duplicate_groups_verified')):
        block.append('退旧前完整用途清单未核验')
    cap=plan['capacity']
    if not cap['verified'] or cap['active_count']!=len(plan['slots']) or cap['active_count']>cap['maximum']:
        block.append('退旧前配置计数或容量矛盾')
    profile=profiles.get(account)
    if not profile or not profile['verified'] or not profile['complete'] or not 0<=(now-_clock(profile['observed_at'])).total_seconds()<=rules['max_age_seconds'] or abs((_clock(profile['observed_at'])-_clock(plan['observed_at'])).total_seconds())>1:
        block.append('退旧前账户重复分组计数缺失、过期或截点不一致')
    else:
        counts={}
        for p in plans:
            if p['account_id']==account:
                for s in p['slots']:counts[s['duplicate_group_id']]=counts.get(s['duplicate_group_id'],0)+1
        if any(profile['counts'].get(g,-1)<n for g,n in counts.items()):block.append('账户重复数小于当前已知用途数')
        if any(n>3 for n in profile['counts'].values()):block.append('账户重复分组已超用户上限，需先复核整体范围')
    if plan['multi_aweme'] and plan['scene']=='live':block.append('直播多号退旧合同未核验')
    if not plan['multi_aweme'] and len({s['aweme_uid'] for s in plan['slots'] if s.get('aweme_uid')})>1:
        block.append('单号声明与多UID清单冲突')
    if any(a.get('account_id')==account and a.get('priority')=='P0' for a in evaluation.get('alerts',[])):
        block.append('账户存在P0风险，不能继续常规退旧')
    kind=selected['material_type']
    def same_use(slot):
        return slot.get('product_id')==selected.get('product_id') and slot.get('aweme_uid')==selected.get('aweme_uid')
    old=[s for s in plan['slots'] if s['material_type']==kind and same_use(s) and s['native']==selected['old_native']
         and (kind=='TITLE' or s.get('material_id')==selected['old_material_id'])]
    new=[s for s in plan['slots'] if s['material_type']==kind and same_use(s) and s['native']==selected['new_native']]
    if len(old)!=1 or len(new)!=1:block.append('新旧素材及具体用途没有一一绑定');return out
    old,new=old[0],new[0]
    uses=[s for s in plan['slots'] if (kind=='TITLE' and s['material_type']=='TITLE' and s['native']['title']==old['native']['title'])
          or (kind!='TITLE' and s.get('material_id')==old.get('material_id'))]
    if len(uses)!=1:block.append('旧素材跨用途复用，不能整体退旧')
    if old['delete_class']!='SELF_PROVIDED' or not old['deletion_eligibility_verified'] or not old['mapping_verified']:
        block.append('旧素材删除来源或映射未核验')
    if _asset_key(kind,old['native'])==_asset_key(kind,new['native']):block.append('替换素材与旧素材是同一原生对象')
    status_at=_time(new.get('status_at'))
    if not new['mapping_verified'] or new.get('available') is not True or new.get('status_verified') is not True or not new.get('status_source') or status_at is None or not 0<=(now-status_at).total_seconds()<=rules['max_age_seconds']:
        block.append('新素材当前可用状态、时间或映射缺失')
    row,errors=_bound_record(old['snapshot_id'],account,'creative',index,evaluation,rules,accounts,now);block.extend(errors)
    if row:
        ctx=row['raw'].get('context',{})
        if ctx.get('parent_plan_id')!=pid or row['raw']['scene']!=draft['scene'] or row['raw']['promotion_type']!='universal':
            block.append('旧弱项当前母计划或产品类型不一致')
        if _time(ctx.get('created_at'))!=_time(selected.get('weak_created_at')):block.append('旧弱项创建身份已变化')
        key=':'.join(map(str,row['key'][3:]))
        if not any(r.get('eligible') is True and r.get('account_id')==account and r.get('scope')=='creative'
                   and r.get('entity_id')==row['raw']['entity_id'] and r.get('source_key')==key
                   and r.get('rule') in WEAK_RULES and r.get('action') in WEAK_ACTIONS for r in evaluation.get('recommendations',[])):
            block.append('旧素材已无当前合格弱项建议，不能沿用创建时结论')
        if parent and row['source']['kind']!=parent['source']['kind']:block.append('新旧父子来源不一致')
        for h in index['by_key'][row['key']]:
            at=h['data'] or h['captured']
            if row['data'] is None or not 0<=(row['data']-at).total_seconds()<=10800:continue
            hc=h['raw'].get('context',{})
            if h['data'] is None or hc.get('creative_type')!='programmatic' or hc.get('parent_plan_id')!=pid or _time(hc.get('created_at'))!=_time(ctx.get('created_at')):
                block.append('当前弱项观察窗口缺时间或关系发生变化');break
    if not block:out.update(local_eligible=True,request=draft['requests']['retire'])
    return out
