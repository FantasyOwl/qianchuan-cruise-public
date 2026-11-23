"""Local, deterministic programmatic-creative rotation drafts.

No network, storage, credentials, permission or execution. Registry facts are
supplied declarations. Prepared wire JSON is review material, never a permit.
"""
import hashlib
import json
import re

from .engine import _record, _settings, _time
from .official_reports import _document, _guard
from .schema import now_cn, parse_time

FORMAT='qianchuan_creative_rotation_registry_v1'
ADD='/open_api/v1.0/qianchuan/uni_promotion/ad/material/add/'
DELETE='/open_api/v1.0/qianchuan/uni_promotion/ad/material/delete/'
REFERENCES=[
    'https://open.oceanengine.com/labels/12/docs/1804363488115850',
    'https://open.oceanengine.com/labels/12/docs/1835232814536707',
    'https://open.oceanengine.com/labels/12/docs/1804363891396633',
]
WEAK_RULES={'creative_ctr_decay_3h','creative_composite_decline','cold_ctr_below_industry'}
WEAK_ACTIONS={'replace_creative','refresh_creatives','review_cold_creative'}
TYPES={'VIDEO','IMAGE','TITLE','CAROUSEL','LIVE_ROOM'}


def _exact(value,required,optional=()):
    if not isinstance(value,dict) or set(value)-set(required)-set(optional) or not set(required)<=set(value):
        raise ValueError('素材替换登记结构缺失或包含未知字段')


def _text(value,limit=500):
    if not isinstance(value,str) or not value or len(value)>limit or any(ord(c)<32 for c in value):
        raise ValueError('素材登记文本或标识无效')
    return value


def _id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[1-9][0-9]{0,19}',value) or int(value)>2**64-1:
        raise ValueError('账户、计划、素材及商品数字ID须为无损十进制字符串')
    return value


def _boolean(value):
    if type(value) is not bool:raise ValueError('核验、范围和启用标记须明确为布尔值')
    return value


def _clock(value):
    _text(value,80)
    return parse_time(value)


def _native(kind,payload,scene):
    if not isinstance(kind,str) or kind not in TYPES:raise ValueError('素材类型未支持')
    if kind=='LIVE_ROOM':
        _exact(payload,set());return {}
    if kind=='VIDEO':
        _exact(payload,set(),{'video_id','aweme_item_id','video_cover_id','image_mode','star_traffic'})
        if ('video_id' in payload)==('aweme_item_id' in payload):
            raise ValueError('视频必须明确选择素材库ID或抖音视频ID，不能按平台忽略顺序猜测')
        out={}
        if 'video_id' in payload:
            out['video_id']=_text(payload['video_id'])
            if not isinstance(payload.get('image_mode'),str) or payload['image_mode'] not in {'VIDEO_LARGE','VIDEO_VERTICAL'}:raise ValueError('素材库视频需明确横版或竖版')
        else:out['aweme_item_id']=_id(payload['aweme_item_id'])
        if 'image_mode' in payload:
            if not isinstance(payload['image_mode'],str) or payload['image_mode'] not in {'VIDEO_LARGE','VIDEO_VERTICAL'}:raise ValueError('视频画幅无效')
            out['image_mode']=payload['image_mode']
        if 'video_cover_id' in payload:out['video_cover_id']=_text(payload['video_cover_id'])
        if 'star_traffic' in payload:
            if type(payload['star_traffic']) is not int or payload['star_traffic']!=1:raise ValueError('星图视频标记无效')
            out['star_traffic']=1
        return out
    if kind=='TITLE':
        _exact(payload,{'title'}, {'title_type'} if scene=='product' else ())
        out={'title':_text(payload['title'])}
        if scene=='product':
            if not isinstance(payload.get('title_type'),str) or payload['title_type'] not in {'COMMODITY_CARD','CUSTOM'}:raise ValueError('商品标题需明确标题类型')
            out['title_type']=payload['title_type']
        return out
    if kind=='IMAGE':
        _exact(payload,{'image_ids','image_mode'})
        ids=payload['image_ids']
        if not isinstance(ids,list) or not 1<=len(ids)<=9:raise ValueError('每组图片接受1至9个已知ID（本地限制）')
        ids=[_text(v) for v in ids]
        if len(set(ids))!=len(ids) or not isinstance(payload['image_mode'],str) or payload['image_mode'] not in {'SQUARE','RECTANGLE'}:raise ValueError('图片ID重复或画幅无效')
        return {'image_ids':ids,'image_mode':payload['image_mode']}
    _exact(payload,set(),{'carousel_id','aweme_carousel_id'})
    if len(payload)!=1:raise ValueError('图文须明确素材库或抖音图文ID，不能同时提供')
    key=next(iter(payload));return {key:_id(payload[key])}


def _asset_key(kind,native):
    if kind=='VIDEO':
        key='video_id' if 'video_id' in native else 'aweme_item_id'
        return kind,key,native[key]
    if kind=='TITLE':return kind,native['title']
    if kind=='IMAGE':return kind,tuple(sorted(native['image_ids']))
    if kind=='CAROUSEL':return kind,*next(iter(native.items()))
    return kind,


def _registry(payload,settings):
    if isinstance(payload,dict) and 'document_text' in payload:
        if not isinstance(payload['document_text'],str) or len(payload['document_text'].encode())>2*1024*1024:
            raise ValueError('素材替换登记文件不能超过2MB')
    payload=_document(payload);_guard(payload);_exact(payload,{'format','plans','duplicate_inventory'})
    if payload['format']!=FORMAT or not isinstance(payload['plans'],list) or not 1<=len(payload['plans'])<=100:
        raise ValueError('请提供1至100个计划的素材替换登记（本地限制）')
    accounts={a['id']:a for a in settings.get('accounts',[]) if a.get('observe',True)}
    profiles=payload['duplicate_inventory'];dup={}
    if not isinstance(profiles,list) or len(profiles)>100:raise ValueError('账户同质分组清单无效')
    for profile in profiles:
        _exact(profile,{'account_id','observed_at','source','verified','complete','groups'})
        aid=_id(profile['account_id'])
        if aid not in accounts or aid in dup:raise ValueError('账户同质分组未启用或重复声明')
        _clock(profile['observed_at']);_text(profile['source']);_boolean(profile['verified']);_boolean(profile['complete'])
        groups=profile['groups'];counts={}
        if not isinstance(groups,list) or len(groups)>2000:raise ValueError('同质分组数量超出本地限制')
        for g in groups:
            _exact(g,{'group_id','active_count'});gid=_text(g['group_id'],150)
            if gid in counts or type(g['active_count']) is not int or not 0<=g['active_count']<=10000000:
                raise ValueError('同质分组计数无效或同组重复，不能选择有利值')
            counts[gid]=g['active_count']
        dup[aid]={**profile,'counts':counts}
    plans=[];seen=set()
    for raw in payload['plans']:
        _exact(raw,{'account_id','plan_id','parent_snapshot_id','observed_at','inventory_verified','inventory_complete',
            'inventory_source','duplicate_groups_verified','multi_aweme','capacity','slots','pool'})
        account,plan=_id(raw['account_id']),_id(raw['plan_id'])
        if account not in accounts or (account,plan) in seen:raise ValueError('账户未启用或计划登记重复')
        seen.add((account,plan));scene=accounts[account]['scene']
        _text(raw['parent_snapshot_id'],200);_clock(raw['observed_at']);_text(raw['inventory_source'])
        for key in ('inventory_verified','inventory_complete','duplicate_groups_verified','multi_aweme'):_boolean(raw[key])
        capacity=raw['capacity'];_exact(capacity,{'unit','active_count','maximum','verified','source'})
        if capacity['unit']!='configured_entries' or any(type(capacity[k]) is not int or not 0<=capacity[k]<=10000 for k in ('active_count','maximum')):
            raise ValueError('容量须明确为配置条目数；不使用加热配额替代')
        _boolean(capacity['verified']);_text(capacity['source'])
        slots=raw['slots'];pool=raw['pool']
        if not isinstance(slots,list) or not isinstance(pool,list) or len(slots)>1000 or len(pool)>1000:
            raise ValueError('每计划素材及候选池各最多1000条（本地限制）')
        clean=[]
        for slot in slots:
            _exact(slot,{'snapshot_id','material_type','native','duplicate_group_id','mapping_verified','delete_class','deletion_eligibility_verified'},
                   {'material_id','product_id','aweme_uid','available','status_at','status_source','status_verified'})
            if slot['snapshot_id'] is not None:_text(slot['snapshot_id'],200)
            kind=slot['material_type'];_text(slot['duplicate_group_id'],150)
            _boolean(slot['mapping_verified']);_boolean(slot['deletion_eligibility_verified'])
            for flag in ('available','status_verified'):
                if flag in slot:_boolean(slot[flag])
            if 'status_at' in slot:_clock(slot['status_at'])
            if 'status_source' in slot:_text(slot['status_source'])
            if not isinstance(slot['delete_class'],str) or slot['delete_class'] not in {'SELF_PROVIDED','SMART','UNKNOWN'}:raise ValueError('可删除来源类别无效')
            native=_native(kind,slot['native'],scene)
            if kind not in {'TITLE','LIVE_ROOM'}:_id(slot.get('material_id'))
            elif 'material_id' in slot:raise ValueError('标题和直播画面不按普通素材ID退旧')
            if scene=='product':_id(slot.get('product_id'))
            if 'aweme_uid' in slot:_id(slot['aweme_uid'])
            if raw['multi_aweme'] and scene=='product':_id(slot.get('aweme_uid'))
            clean.append({**slot,'native':native})
        candidates=[];candidate_ids=set()
        for candidate in pool:
            _exact(candidate,{'id','material_type','native','duplicate_group_id','enabled','reviewed','reviewed_at','review_source','queued_at'},
                   {'product_id','aweme_uid'})
            cid=_text(candidate['id'],150)
            if cid in candidate_ids:raise ValueError('候选池ID重复')
            candidate_ids.add(cid);_text(candidate['duplicate_group_id'],150);_text(candidate['review_source'])
            _clock(candidate['reviewed_at']);_clock(candidate['queued_at'])
            for flag in ('enabled','reviewed'):_boolean(candidate[flag])
            kind=candidate['material_type']
            if kind=='LIVE_ROOM':raise ValueError('当前合同不创建替换直播间画面')
            native=_native(kind,candidate['native'],scene)
            if scene=='product':_id(candidate.get('product_id'))
            if 'aweme_uid' in candidate:_id(candidate['aweme_uid'])
            if raw['multi_aweme'] and scene=='product':_id(candidate.get('aweme_uid'))
            candidates.append({**candidate,'native':native})
        plans.append({**raw,'slots':clean,'pool':candidates,'scene':scene})
    return plans,dup


def _bound_record(snapshot_id,account,scope,index,evaluation,rules,accounts,now):
    matches=[r for r in index['by_id'].get(snapshot_id,[]) if r['raw'].get('account_id')==account and r['raw'].get('scope')==scope]
    if len(matches)!=1:return None,['绑定快照缺失、重复或账户/层级不符']
    row=matches[0]
    key=':'.join(map(str,row['key'][3:]))
    health=[h for h in evaluation.get('collection_health',{}).get('sources',[]) if h['account_id']==account and h['scope']==scope and h['entity_id']==row['raw']['entity_id'] and h['source_key']==key]
    blockers=[]
    if len(health)!=1 or health[0].get('snapshot_id')!=snapshot_id or health[0].get('clock_current') is not True:
        blockers.append('绑定对象不是最新且时间有效的独立来源')
    if row['source'].get('kind') not in {'import','demo'} or row['source'].get('coverage')!='report' or row['source'].get('metric_period')!='day':
        blockers.append('素材替换需要可核对完整报告，不以可见行或单场数据授权')
    if any(x!='ROI 口径未确认' for x in row['issues']):blockers.append('绑定来源存在未解决的数据质量问题')
    ctx=row['raw'].get('context',{})
    ctx=ctx if isinstance(ctx,dict) else {}
    if ctx.get('creative_type')!='programmatic':blockers.append('未明确声明程序化创意模式；不从字段名或AUTO推断')
    if row['control'].get('running') is not True or row['control'].get('history_known') is not True or row['control'].get('guarantee_verified') is not True or ctx.get('manual_reviewed') is not True:
        blockers.append('开启、历史、保障或人工核验声明不足')
    changed=_time(row['control'].get('last_change_at'))
    if (changed is None and row['control'].get('changes_today')!=0) or (changed and (now-changed).total_seconds()<max(60,rules['cooldown_minutes'])*60):
        blockers.append('同计划最近调整未知或未满足至少一小时冷却')
    peers=[r for r in index['by_key'][row['key']] if r['captured']==row['captured']]
    signatures=set()
    for r in peers:
        c=r['raw'].get('context',{});c=c if isinstance(c,dict) else {}
        signatures.add(tuple(repr(v) for v in (r['data'],r['control'].get('running'),r['control'].get('history_known'),
            r['control'].get('guarantee_verified'),r['control'].get('last_change_at'),r['control'].get('changes_today'),
            c.get('creative_type'),c.get('parent_plan_id'),c.get('manual_reviewed'),r['source'].get('coverage'),r['issues'])))
    if len(signatures)!=1:blockers.append('最新同次绑定观测的状态、关系或质量声明冲突')
    return row,blockers


def _wire(native):
    return {k:int(v) if k in {'aweme_item_id','carousel_id','aweme_carousel_id'} else v for k,v in native.items()}


def _request(path,body,*,synthetic=False):
    return {'method':'POST','url':None if synthetic else 'https://api.oceanengine.com'+path,
            'documented_endpoint':path,'synthetic_only':synthetic,
            'body_json':json.dumps(body,ensure_ascii=False,separators=(',',':'),allow_nan=False),
            'preview_only':True,'executable':False}


def preview_creative_rotations(payload,snapshots,settings,evaluation,now=None):
    """Choose at most one explicit weak asset and unused candidate per plan."""
    now=now or now_cn();rules,accounts=_settings(settings);plans,duplicate_inventory=_registry(payload,settings);drafts=[]
    known_counts={};reserved_groups={}
    for p in plans:
        for slot in p['slots']:
            key=p['account_id'],slot['duplicate_group_id'];known_counts[key]=known_counts.get(key,0)+1
    index={'by_id':{},'by_key':{}}
    for raw in snapshots:
        try:r=_record(raw,accounts,now)
        except (ValueError,TypeError,AttributeError,OverflowError):r=None
        if r is not None:
            if isinstance(raw.get('id'),str):index['by_id'].setdefault(raw['id'],[]).append(r)
            index['by_key'].setdefault(r['key'],[]).append(r)
    def plan_priority(plan):
        identities=set()
        for slot in plan['slots']:
            for r in index['by_id'].get(slot['snapshot_id'],[]):
                if r['raw']['account_id']==plan['account_id'] and r['raw']['scope']=='creative':
                    identities.add((r['raw']['entity_id'],':'.join(map(str,r['key'][3:]))))
        priorities=[r['priority'] for r in evaluation.get('recommendations',[]) if r.get('eligible') is True
            and r.get('account_id')==plan['account_id'] and r.get('action') in WEAK_ACTIONS and r.get('rule') in WEAK_RULES
            and (r.get('entity_id'),r.get('source_key')) in identities]
        return min(priorities,default='P9'),int(plan['account_id']),int(plan['plan_id'])
    plans=sorted(plans,key=plan_priority)
    for plan in plans:
        account,pid=plan['account_id'],plan['plan_id'];block=[]
        out={'account_id':account,'plan_id':pid,'scene':plan['scene'],'local_eligible':False,'state':'blocked',
             'blockers':block,'selection':None,'requests':None,'stages':[],'execution_eligible':False,
             'executed':False,'platform_verified':False,'automatic_retry_allowed':False,'can_release_hold':False,
             'parent_plan_reservation_required':True,'rebuild_before_submission_required':True}
        drafts.append(out)
        parent,missing=_bound_record(plan['parent_snapshot_id'],account,'plan',index,evaluation,rules,accounts,now);block.extend(missing)
        if parent and parent['raw']['entity_id']!=pid:block.append('母计划ID与绑定快照不一致')
        if parent and parent['raw']['scene']!=plan['scene']:block.append('账户与母计划场景不一致')
        if parent and parent['raw']['promotion_type']!='universal':block.append('标准、搜索和乘方尚无本模块已核验的完整添加/退旧合同')
        if not all(plan[k] is True for k in ('inventory_verified','inventory_complete','duplicate_groups_verified')):
            block.append('完整用途清单或同质分组未声明核验')
        profile=duplicate_inventory.get(account)
        if not profile or profile['verified'] is not True or profile['complete'] is not True:
            block.append('账户级完整同质分组计数缺失或未核验；不按单计划推断全账户重复数')
        elif (not 0<=(now-_clock(profile['observed_at'])).total_seconds()<=rules['max_age_seconds']
              or abs((_clock(profile['observed_at'])-_clock(plan['observed_at'])).total_seconds())>1):
            block.append('账户分组计数过期或与计划配置截点不一致')
        elif any(profile['counts'].get(group,-1)<count for (aid,group),count in known_counts.items() if aid==account):
            block.append('账户分组累计小于本次已知计划条目，不能采用矛盾清单')
        if parent and (not parent['data'] or abs((_clock(plan['observed_at'])-parent['data']).total_seconds())>1):
            block.append('配置清单时间与母计划独立数据截点不一致')
        if plan['capacity']['verified'] is not True or plan['capacity']['active_count']!=len(plan['slots']):
            block.append('配置容量未核验或当前条目计数不一致')
        if plan['capacity']['active_count']+1>plan['capacity']['maximum']:
            block.append('没有先添加的容量，不能先删旧素材腾位')
        if plan['multi_aweme'] and plan['scene']=='live':block.append('直播多号的具体添加绑定尚未核验，不猜测抖音号归属')
        if not plan['multi_aweme'] and len({s['aweme_uid'] for s in plan['slots'] if s.get('aweme_uid')})>1:
            block.append('清单包含多个抖音UID却声明单号，不能选择有利的范围声明')
        if any(a.get('account_id')==account and a.get('priority')=='P0' for a in evaluation.get('alerts',[])):
            block.append('账户存在P0风险，先处理风险再复核素材替换')
        weak=[]
        for slot in plan['slots']:
            if not slot['snapshot_id']:continue
            row,errors=_bound_record(slot['snapshot_id'],account,'creative',index,evaluation,rules,accounts,now)
            if row is None:continue
            ctx=row['raw'].get('context',{})
            ctx=ctx if isinstance(ctx,dict) else {}
            if ctx.get('parent_plan_id')!=pid or row['raw']['scene']!=plan['scene']:continue
            if parent and row['raw']['promotion_type']!=parent['raw']['promotion_type']:
                errors.append('弱创意与母计划推广产品类型不一致')
            if parent and row['source']['kind']!=parent['source']['kind']:
                errors.append('演示与真实报告来源不能拼接为一个替换方案')
            key=':'.join(map(str,row['key'][3:]))
            found=[r for r in evaluation.get('recommendations',[]) if r.get('eligible') is True and r.get('scope')=='creative'
                and r.get('account_id')==account and r.get('entity_id')==row['raw']['entity_id'] and r.get('source_key')==key
                and r.get('action') in WEAK_ACTIONS and r.get('rule') in WEAK_RULES]
            for rec in found:
                history=index['by_key'][row['key']]
                for h in history:
                    if row['data'] is None:continue
                    at=h['data'] or h['captured']
                    if not 0<=(row['data']-at).total_seconds()<=10800:continue
                    if h['data'] is None:
                        errors.append('弱项观察窗口存在缺失数据时间的采样');break
                    hc=h['raw'].get('context',{});hc=hc if isinstance(hc,dict) else {}
                    if hc.get('creative_type')!='programmatic' or hc.get('parent_plan_id')!=pid or _time(hc.get('created_at'))!=_time(ctx.get('created_at')):
                        errors.append('弱项观察窗口内模式或母计划关系变化，不能拼接历史表现');break
                weak.append((rec['priority'],rec['id'],slot,row,errors))
        if not weak:block.append('没有与清单绑定、当前合格的弱创意建议')
        if not weak:continue
        _,rec_id,old,row,weak_errors=min(weak,key=lambda x:(x[0],x[1]));block.extend(weak_errors)
        if old['mapping_verified'] is not True:block.append('业绩对象与原生素材的映射未核验')
        if old['delete_class']!='SELF_PROVIDED' or old['deletion_eligibility_verified'] is not True:
            block.append('未核验为可删除的自提素材，不能把CUSTOM/程序化模式当作删除资格')
        kind=old['material_type']
        if kind=='LIVE_ROOM' or plan['scene']=='live' and kind not in {'VIDEO','TITLE'}:block.append('该场景素材类型没有完整添加/退旧合同')
        if kind=='IMAGE' and len(old['native']['image_ids'])!=1:
            block.append('多图片ID与旧单素材ID的对应范围尚未核验，不能按一个ID退旧')
        uses=[s for s in plan['slots'] if (kind=='TITLE' and s['material_type']=='TITLE' and s['native']['title']==old['native']['title'])
            or (kind not in {'TITLE','LIVE_ROOM'} and s.get('material_id')==old.get('material_id'))]
        if len(uses)!=1:block.append('旧素材在多个用途复用，不能按单个弱项建议批量退旧')
        videos=[s for s in plan['slots'] if s['material_type']=='VIDEO']
        if plan['scene']=='live' and kind=='TITLE' and (not videos or all('aweme_item_id' in s['native'] for s in videos)):
            block.append('直播视频全部为抖音主页视频或范围不明，不添加标题')
        existing={_asset_key(s['material_type'],s['native']) for s in plan['slots']}
        options=[]
        for c in plan['pool']:
            if not c['enabled'] or not c['reviewed'] or c['material_type']!=kind:continue
            if kind=='IMAGE' and len(c['native']['image_ids'])!=1:continue
            reviewed,queued=_clock(c['reviewed_at']),_clock(c['queued_at'])
            if not 0<=(now-reviewed).total_seconds()<=rules['max_age_seconds'] or queued>reviewed:continue
            if c.get('product_id')!=old.get('product_id') or c.get('aweme_uid')!=old.get('aweme_uid'):continue
            if _asset_key(kind,c['native']) in existing:continue
            group=c['duplicate_group_id'];count=profile['counts'].get(group) if profile else None
            if count is None or count+reserved_groups.get((account,group),0)>=3:continue
            options.append(c)
        if not options:block.append('没有近期审核、同用途、未在投且加入后重复不超过3条的替换候选');continue
        candidate=min(options,key=lambda c:(_clock(c['queued_at']),c['id']))
        out['selection']={'weak_proposal_id':rec_id,'snapshot_id':row['raw']['id'],
            'weak_created_at':row['raw'].get('context',{}).get('created_at'),
            'material_type':kind,'old_material_id':old.get('material_id'),'old_native':old['native'],
            'candidate_id':candidate['id'],'new_native':candidate['native'],'product_id':old.get('product_id'),
            'aweme_uid':old.get('aweme_uid'),'additional_weak_options':len(weak)-1,
            'selection_method':'oldest_reviewed_queue_then_candidate_id','declared_duplicate_group':candidate['duplicate_group_id'],
            'duplicate_scope':'account','declared_group_count_before':profile['counts'][candidate['duplicate_group_id']],
            'planned_group_additions_before':reserved_groups.get((account,candidate['duplicate_group_id']),0)}
        if block:continue
        body={'advertiser_id':int(account),'ad_id':int(pid)}
        field={'VIDEO':'video_material','IMAGE':'image_material','TITLE':'title_material','CAROUSEL':'carousel_material'}[kind]
        if plan['scene']=='live':body['programmatic_creative_media_list']={field:[_wire(candidate['native'])]}
        else:
            item={'product_id':int(old['product_id']),field:[_wire(candidate['native'])]}
            if old.get('aweme_uid'):item['aweme_uid']=int(old['aweme_uid'])
            body['multi_product_creative_list']=[item]
        retire={'advertiser_id':int(account),'ad_id':int(pid),
                **({'titles':[old['native']['title']]} if kind=='TITLE' else {'material_ids':[int(old['material_id'])]})}
        synthetic=parent['source']['kind']=='demo'
        out.update(local_eligible=True,state='local_draft_ready',synthetic_only=synthetic,
            requests={'add':_request(ADD,body,synthetic=synthetic),'retire':_request(DELETE,retire,synthetic=synthetic)})
        out['stages']=[
            {'action':'add','object':candidate['id'],'fresh_evaluation_required':True,'execution_allowed_now':False},
            {'action':'verify_add','requires':'明确同计划/商品/抖音号的新素材配置和可用状态；code=0回执本身不足'},
            {'action':'cooldown','minimum_seconds':max(60,rules['cooldown_minutes'])*60,'other_plan_changes_forbidden':True},
            {'action':'retire','object':old.get('material_id') or old['native']['title'],'fresh_evaluation_required':True,
             'requires':'重新取得完整用途清单、替换素材状态及当前弱项证据','execution_allowed_now':False},
            {'action':'verify_retire','requires':'完整配置的独立前后核对；有消耗素材列表缺失不证明退旧成功'},
            {'action':'observe','minimum_seconds':7200,'parameter_basis':'本地素材更新观察保护，不是官方配额或保障规则'},
        ]
        group_key=account,candidate['duplicate_group_id'];reserved_groups[group_key]=reserved_groups.get(group_key,0)+1
        out['intent_digest']=hashlib.sha256(json.dumps({'plan':plan,'profile':profile,'draft':out,
            'settings':settings},ensure_ascii=False,sort_keys=True,allow_nan=False).encode()).hexdigest()
    result={'format':FORMAT,'planner_version':'2026-10-23-rotation-draft-v1','checked_at':now.isoformat(),'drafts':drafts,'references':REFERENCES,
        'persisted':False,'executed':False,'ai_sent':False,'platform_verified':False,
        'workflow_runner_implemented':False,'native_material_submission_enabled':False,
        'limits':['登记信息属于人工声明，不认证平台权限或真实素材状态。','每计划只准备一个弱项的小步替换；未实现持久编排、实际读回和提交。',
            '先添加后核对，至少一小时冷却后重新评估退旧；未知结果不重试，不自动解除锁定。',
            '标准/搜索/乘方及多用途退旧等未核验范围保持阻断，不猜造通用接口。',
            '重复不超过3按账户累计作保守解释，本次草稿按优先级和计划ID顺序合并拟新增数；不是跨请求持久预留或平台认证。']}
    result['preview_digest']=hashlib.sha256(json.dumps(result,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return result
