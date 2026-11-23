"""Explicit local account registry and revisioned browser read scope.

Registry edits are observation configuration, never platform account operations.
Existing targets are preserved; newly added accounts have no ROI target.
"""
import copy
import hashlib
import json
import re

from .schema import parse_time, now_cn
from .delivery_entry import delivery_entry, ENTRIES

MAX_ACCOUNTS=100
LEGACY_SCOPE=[{'id':'1999999999999001','scene':'product','tab_group':'示例分组A'},
              {'id':'1999999999999002','scene':'live','tab_group':'示例分组A'}]


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def _text(value,label,limit):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or any(ord(c)<32 for c in value):
        raise ValueError(label+'无效')
    return value


def registry_accounts(settings):
    result=[]
    for account in settings.get('accounts',[]):
        old=next((a for a in LEGACY_SCOPE if a['id']==account['id']),None)
        result.append({'id':account['id'],'name':account.get('name',account['id']),'scene':account['scene'],
            'delivery_entry':delivery_entry(account),
            'tab_group':account.get('tab_group',old['tab_group'] if old else ''),
            'observe':account.get('observe',old is not None) is True})
    return result


def collection_policy(settings):
    accounts=[{k:a[k] for k in ('id','scene','tab_group')} for a in registry_accounts(settings) if a['observe']]
    if any(not a['tab_group'] or a['scene'] not in {'live','product'} for a in accounts):
        raise ValueError('启用采集的账户必须指定分组与场景')
    accounts.sort(key=lambda a:a['id'])
    return {'protocol':2,'revision':_digest(accounts),'accounts':accounts}


def registry_view(settings,diagnostic=None,now=None,*,api_connected=False):
    accounts=registry_accounts(settings);policy=collection_policy(settings);d=diagnostic or {};now=now or now_cn()
    try:
        seen=parse_time(d.get('last_seen'));age=(now-seen).total_seconds()
    except (ValueError,TypeError,AttributeError):age=None
    ready=(d.get('collector')=='extension' and d.get('collector_protocol')==2
        and d.get('policy_revision')==policy['revision'] and age is not None and -120<=age<=180)
    return {'accounts':accounts,'revision':_digest(settings.get('accounts',[])),
            'policy_revision':policy['revision'],'collector_ready_for_scope_changes':ready,'max_accounts':MAX_ACCOUNTS,
            'api_connected':api_connected is True,'scope_changes_available':ready or api_connected is True,
            'delivery_entry_options':[{'value':key,'label':label} for key,label in ENTRIES.items()]}


def replace_registry(settings,payload,diagnostic=None,now=None,*,api_connected=False):
    if not isinstance(payload,dict) or set(payload)!={'accounts','expected_revision'}:
        raise ValueError('账户配置请求必须包含accounts和expected_revision')
    current=registry_view(settings,diagnostic,now)
    if payload['expected_revision']!=current['revision']:raise ValueError('账户或目标已变化，请重新载入后保存')
    rows=payload['accounts']
    if not isinstance(rows,list) or not 1<=len(rows)<=MAX_ACCOUNTS:raise ValueError('请保留1至100个账户，可单独关闭采集')
    old={a['id']:a for a in settings['accounts']};seen=set();accounts=[]
    for row in rows:
        if not isinstance(row,dict) or set(row)-{'id','name','scene','tab_group','observe','delivery_entry'} or not {'id','name','scene','tab_group','observe'}<=set(row):
            raise ValueError('账户配置字段无效；目标和平台操作不在此入口修改')
        aid=row['id']
        if not isinstance(aid,str) or not re.fullmatch(r'[0-9]{1,30}',aid) or aid in seen:raise ValueError('账户ID须为不重复的数字字符串')
        if not isinstance(row['scene'],str) or row['scene'] not in {'live','product'} or type(row['observe']) is not bool:raise ValueError('账户场景或采集开关无效')
        name=_text(row['name'],'账户名称',80)
        group=_text('API账户' if api_connected and row['tab_group']=='' else row['tab_group'],'Chrome分组名称',100)
        seen.add(aid)
        value=copy.deepcopy(old.get(aid,{'target_roi':None,'roi_basis':'overall_payment'}))
        entry=delivery_entry(row if 'delivery_entry' in row else old.get(aid,{}))
        value.update(id=aid,name=name,scene=row['scene'],tab_group=group,observe=row['observe'],delivery_entry=entry);accounts.append(value)
    cfg=copy.deepcopy(settings);cfg['accounts']=accounts
    if collection_policy(cfg)['revision']!=current['policy_revision'] and not (current['collector_ready_for_scope_changes'] or api_connected):
        raise ValueError('请先重新加载支持账户配置的采集器并确认通信，再修改采集账户或分组')
    return cfg


def validate_collection_batch(settings,payload):
    policy=collection_policy(settings)
    if 'collector_protocol' not in payload:
        if policy['accounts']!=LEGACY_SCOPE:raise ValueError('旧采集器不支持当前采集范围，请重新加载新版扩展')
        return
    if type(payload.get('collector_protocol')) is not int or payload['collector_protocol']!=2 or payload.get('policy_revision')!=policy['revision']:
        raise ValueError('采集账户配置已变化，本轮结果未保存')
    allowed={a['id'] for a in policy['accounts']}
    if any(not isinstance(r,dict) or r.get('account_id') not in allowed for r in payload.get('snapshots',[])):
        raise ValueError('采集结果包含未启用的账户')
