"""Current plan-detail contract and a disconnected, default-off GET adapter.

A fetched configuration is not an execution acknowledgement or a performance
clock. No result here changes submission state, releases a hold or retries IO.
"""
from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

from .actuation import BASE_URL
from .accounts import registry_accounts
from .plan_readback import _business_data, _clock, _id, _compare_values
from .schema import now_cn


DETAIL_PATH = '/open_api/v1.0/qianchuan/uni_promotion/ad/detail/'
DETAIL_SOURCE = 'https://open.oceanengine.com/labels/12/docs/1804362305657868'


def compare_supplied_detail(raw, query_context, attempt, now):
    data = _business_data(raw)
    entity_id = _id(data.get('ad_id'))
    delivery = data.get('delivery_setting', {})
    if not isinstance(delivery, dict):
        raise ValueError('计划详情投放设置结构无效')
    info = {key: data.get(key) for key in ('marketing_goal', 'opt_status', 'adlab_scene')}
    info.update({key: delivery.get(key) for key in ('budget_mode', 'budget', 'roi2_goal', 'smart_bid_type', 'deep_external_action')})
    # This endpoint returns one exact object. An unrelated object is not a
    # missing page and cannot be accepted as a response to this request.
    if not isinstance(attempt, dict) or not isinstance(attempt.get('context'), dict) or entity_id != attempt['context'].get('entity_id'):
        raise ValueError('计划详情ID与待核对提交不一致')
    return _compare_values(raw, query_context, attempt, now, {entity_id: info}, detail=True)


def _detail_url(account_id, entity_id):
    query = urllib.parse.urlencode({'advertiser_id': _id(account_id), 'ad_id': _id(entity_id)})
    return BASE_URL+DETAIL_PATH+'?'+query


def prepare_detail_request(account_id, entity_id, settings):
    account_id, entity_id = _id(account_id), _id(entity_id)
    try:
        rows = registry_accounts(settings)
    except (AttributeError, KeyError, TypeError, ValueError):
        raise ValueError('读取账户配置无效') from None
    matches = [a for a in rows if isinstance(a, dict) and a.get('id') == account_id]
    if (len(matches) != 1 or matches[0].get('observe', True) is not True
            or not isinstance(matches[0].get('scene'), str) or matches[0]['scene'] not in {'live', 'product'}):
        raise ValueError('计划读取须指向唯一且已启用的配置账户')
    url = _detail_url(account_id, entity_id)
    return {'account_id':account_id, 'entity_id':entity_id, 'method':'GET', 'url':url,
            'marketing_goal':'LIVE_PROM_GOODS' if matches[0]['scene']=='live' else 'VIDEO_PROM_GOODS',
            'digest':hashlib.sha256(url.encode()).hexdigest(), 'official_source':DETAIL_SOURCE,
            'request_submitted':False, 'network_enabled':False, 'read_only':True}


@dataclass(frozen=True)
class PlanDetailPermit:
    account_id: str
    request_digest: str
    issued_at: datetime
    expires_at: datetime
    authorization_reference: str


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _get_plan_detail(account_id, entity_id, token, timeout):
    url = _detail_url(account_id, entity_id)
    request = urllib.request.Request(url, headers={'Access-Token':token, 'Cache-Control':'no-cache'}, method='GET')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        if response.geturl() != url:
            raise ValueError('读取响应地址发生变化')
        payload = response.read(1024*1024+1)
        if len(payload) > 1024*1024:
            raise ValueError('读取响应超过大小限制')
        return payload.decode('utf-8')


class PlanDetailClient:
    def __init__(self, *, enable_network=False, transport=None, clock=None):
        if transport is not None and not callable(transport):
            raise ValueError('读取传输器必须是显式可调用对象')
        if clock is not None and not callable(clock):
            raise ValueError('读取时钟必须是可调用对象')
        self.enable_network = enable_network is True
        self.transport = _get_plan_detail if transport is None else transport
        self.custom_transport = transport is not None
        self.clock = now_cn if clock is None else clock

    def review_attempt(self, ledger, attempt_id, settings, permit, token_provider):
        # No credentials, attempt read, transport or audit when disabled.
        if not self.enable_network:
            raise PermissionError('计划详情读取适配器未启用，当前观察服务没有该入口')
        started = _clock(self.clock())
        if (not isinstance(permit, PlanDetailPermit) or not isinstance(permit.issued_at, datetime)
                or not isinstance(permit.expires_at, datetime) or permit.issued_at.tzinfo is None
                or permit.expires_at.tzinfo is None or not permit.issued_at <= started < permit.expires_at
                or not timedelta(0) < permit.expires_at-permit.issued_at <= timedelta(minutes=5)
                or not isinstance(permit.authorization_reference, str) or not permit.authorization_reference.strip()):
            raise PermissionError('需要本次具体读取的有效授权记录')
        with ledger.connect() as db:
            row = db.execute('SELECT created_at,context_json FROM submission_attempts WHERE id=?', (attempt_id,)).fetchone()
        if row is None:
            raise ValueError('找不到待核对的提交记录')
        try:
            if _clock(row[0]) > started:
                raise ValueError('提交时间在未来')
            context = json.loads(row[1] or '{}')
            if not isinstance(context,dict) or context.get('scope','plan') != 'plan':
                raise ValueError('计划详情接口不能读取调控任务')
            request = prepare_detail_request(context['account_id'], context['entity_id'], settings)
        except (ValueError, TypeError, KeyError):
            raise ValueError('提交记录缺少明确且仍在配置范围内的对象') from None
        if permit.account_id != request['account_id'] or permit.request_digest != request['digest']:
            raise PermissionError('读取账户或请求摘要与授权不符')
        token = token_provider()
        if not isinstance(token, str) or not 1 <= len(token) <= 4096 or any(ord(c)<33 or ord(c)>126 for c in token):
            raise ValueError('开放平台凭据格式无效')
        io_at = _clock(self.clock())
        if not started <= io_at < permit.expires_at:
            raise PermissionError('读取授权已过期或本机时钟回退')
        request = prepare_detail_request(request['account_id'], request['entity_id'], settings)
        try:
            raw = self.transport(request['account_id'], request['entity_id'], token, 10)
            received = _clock(self.clock())
            query = {'account_id':request['account_id'], 'marketing_goal':request['marketing_goal'],
                     'request_started_at':io_at.isoformat(), 'received_at':received.isoformat()}
            comparison = ledger.review_supplied_readback(attempt_id, raw, query, received, response_kind='detail')
        except Exception:
            # The error or raw response may echo credentials. Never retain it.
            return {'ok':False, 'read_attempted':True, 'comparison_saved':False, 'read_only':True,
                    'automatic_retry':False, 'submission_state_changed':False,
                    'note':'计划详情读取或核对失败；原提交状态及未知锁定保持不变'}
        return {'ok':True, 'read_attempted':True, 'comparison_saved':True, 'read_only':True,
                'transport_kind':'custom_transport' if self.custom_transport else 'official_https',
                'comparison':comparison, 'automatic_retry':False, 'submission_state_changed':False,
                'note':'已核对返回的配置值；未认证数据时钟、执行归因或解除未知锁定'}
