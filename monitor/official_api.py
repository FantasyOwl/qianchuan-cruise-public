"""Local OAuth connection and read-only Ocean Engine transport.

Credentials never enter reports, logs, API status responses or delivery archives.
Only the fixed OAuth POSTs and business GETs below are implemented here. Existing
browser approvals cannot be submitted through this transport.
"""
from __future__ import annotations

import copy
import ctypes
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from .delivery_entry import delivery_entry, report_entries, ENTRIES, ADLAB_SCENES
from .official_rate import RateGovernor, RateDeferred, default_governor, request_priority


API_HOST = 'https://api.oceanengine.com'
AUTH_HOST = 'https://ad.oceanengine.com'
PATHS = {
    'exchange': (AUTH_HOST, 'POST', '/open_api/oauth2/access_token/'),
    'refresh': (AUTH_HOST, 'POST', '/open_api/oauth2/refresh_token/'),
    'accounts': (API_HOST, 'GET', '/open_api/oauth2/advertiser/get/'),
    'account_report': (API_HOST, 'GET', '/open_api/v1.0/qianchuan/report/all_promotion/get/'),
    'plans': (API_HOST, 'GET', '/open_api/v1.0/qianchuan/uni_promotion/list/'),
    'plan_detail': (API_HOST, 'GET', '/open_api/v1.0/qianchuan/uni_promotion/ad/detail/'),
    'report_config': (API_HOST, 'GET', '/open_api/v1.0/qianchuan/report/uni_promotion/config/get/'),
    'report_data': (API_HOST, 'GET', '/open_api/v1.0/qianchuan/report/uni_promotion/data/get/'),
    'author_report': (API_HOST, 'GET', '/open_api/v1.0/qianchuan/report/uni_promotion/dimension_data/author/get/'),
    'control_tasks': (API_HOST, 'GET', '/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/list/'),
    'plan_materials': (API_HOST, 'GET', '/open_api/v1.0/qianchuan/uni_promotion/ad/material/get/'),
    'live_rooms':(API_HOST,'GET','/open_api/v1.0/qianchuan/today_live/room/get/'),
    'live_config':(API_HOST,'GET','/open_api/v1.0/qianchuan/report/today_live/room/config/get/'),
    'live_data':(API_HOST,'GET','/open_api/v1.0/qianchuan/report/today_live/room/data/get/'),
    'gray':(API_HOST,'GET','/open_api/v1.0/qianchuan/tools/gray/'),
}
WRITE_PATHS = {
    'create_control_task': (API_HOST, 'POST', '/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/create/'),
    'pause_control_task': (API_HOST, 'POST', '/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/status/update/'),
    'pause_plan': (API_HOST, 'POST', '/open_api/v1.0/qianchuan/uni_promotion/ad/status/update/'),
    'update_task_budget': (API_HOST, 'POST', '/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/budget/update/'),
    'update_task_duration': (API_HOST, 'POST', '/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/duration/update/'),
    'update_plan_budget': (API_HOST, 'POST', '/open_api/v1.0/qianchuan/uni_promotion/ad/budget/update/'),
    'update_plan_roi': (API_HOST, 'POST', '/open_api/v1.0/qianchuan/uni_promotion/ad/roi2_goal/update/'),
    'create_smart_control': (API_HOST, 'POST', '/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/smart_control/create/'),
    'stop_smart_control': (API_HOST, 'POST', '/open_api/v1.0/qianchuan/uni_promotion/ad/control_task/smart_control/status/update/'),
}
ACCOUNT_FIELDS = {
    'stat_cost_for_roi2': 'spend', 'total_prepay_and_pay_order_roi2': 'roi',
    'total_pay_order_gmv_include_coupon_for_roi2': 'gmv',
    'total_pay_order_gmv_for_roi2': 'actual_payment_gmv',
    'total_pay_order_count_for_roi2': 'orders',
    'total_cost_per_pay_order_for_roi2': 'conversion_cost',
    'total_prepay_and_pay_settle_roi2_1h': 'net_roi',
    'total_order_settle_amount_for_roi2_1h': 'net_gmv',
    'total_order_settle_count_for_roi2_1h': 'net_orders',
    'total_cost_per_pay_order_settle_for_roi2_1h': 'net_conversion_cost',
    'total_order_real_settle_amount_for_roi2_1h': 'official_actual_net_paid',
    'stat_cost_for_overall_roi2': 'comprehensive_cost',
    'total_prepay_and_pay_settle_overall_roi2_1h': 'marketing_roi',
}
# The live aggregate service accepts these documented fields separately but
# returns 50000 when all thirteen are requested together (verified 2026-10-28).
# Query disjoint metric groups at the same cutoff, then join fields, never ROI.
LIVE_FIELD_GROUPS = (tuple(list(ACCOUNT_FIELDS)[:6]),tuple(list(ACCOUNT_FIELDS)[6:11]),tuple(list(ACCOUNT_FIELDS)[11:]))


class OAuthCallbackError(ValueError):
    def __init__(self, reason, message):
        self.reason = reason
        super().__init__(message)


class OfficialBusinessError(ValueError):
    def __init__(self,code,*,operation=None,reason=None,retry_after=0,hint=None,origin='platform',request_id=None,limit_kind=None):
        self.code=code;self.operation=operation;self.reason=reason;self.retry_after=retry_after
        self.origin=origin;self.hint=hint;self.request_id=request_id
        self.limit_kind=limit_kind or ('platform_total' if code==40100 else 'developer_qps' if code==40110 else 'unknown')
        names={'plan_materials':'素材配置','control_tasks':'调控任务','report_data':'数据报表',
            'report_config':'报表配置','plans':'计划列表','plan_detail':'计划详情','account_report':'账户报表'}
        label=names.get(operation,'官方接口')
        if origin=='local_license':message=hint or '软件授权不可用，请续期后再操作。'
        elif origin.startswith('local_'):message=f'本机等待{label}'+('冷却' if origin=='local_cooldown' else '调用队列')+f'，还需约{retry_after}秒；本次未请求平台（上次状态码{code}）'
        elif reason=='rate_limited':message=label+{'platform_total':'触发平台接口总频控','developer_qps':'触发开发者自身频控','minute_penalty':'触发分钟级频控惩罚','day_penalty':'触发当日频控惩罚'}.get(self.limit_kind,'请求频率超限')+f'（{code}）'+('：'+hint if hint else '')
        elif reason=='service_busy':message=f'{label}暂时繁忙（{code}），稍后按巡航间隔重试'
        elif code==40000 and hint:message=f'{label}参数校验失败（40000）：'+hint
        else:message=label+'未成功，业务码：'+str(code)+('：'+hint if hint else '')
        if request_id and origin=='platform':message+='；请求编号 '+request_id
        super().__init__(message)


def exact_id(value):
    if type(value) not in (str, int) or not re.fullmatch(r'[1-9][0-9]{0,29}', str(value)):
        raise ValueError('ID须为完整十进制整数，不支持浮点数')
    return str(value)


def _secret(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError('凭据格式无效，请在本机重新填写')
    return value


class _Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(raw, decrypt=False):
    if os.name != 'nt':
        raise ValueError('此凭据库需要 Windows 当前用户加密服务')
    source = ctypes.create_string_buffer(raw)
    blob = _Blob(len(raw), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    result = _Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    protect = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    protect.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(_Blob)]
    protect.restype = ctypes.c_int
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not protect(ctypes.byref(blob), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError('本机凭据加密服务失败')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class CredentialVault:
    def __init__(self, path, *, protect=None, unprotect=None):
        self.path = Path(path)
        self.protect = protect or _dpapi
        self.unprotect = unprotect or (lambda raw: _dpapi(raw, True))

    def load(self):
        if not self.path.exists():
            return {'version': 1, 'app': {}, 'grants': {}, 'pending': {}}
        try:
            value = json.loads(self.unprotect(self.path.read_bytes()))
            if value.get('version') != 1 or not all(isinstance(value.get(k), dict) for k in ('app', 'grants', 'pending')):
                raise ValueError()
            return value
        except Exception:
            raise ValueError('本机API凭据库无法解密，请使用原Windows用户运行') from None

    def save(self, value):
        encrypted = self.protect(json.dumps(value, ensure_ascii=False, allow_nan=False).encode())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        with open(temp, 'wb') as file:
            file.write(encrypted)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, self.path)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OfficialTransport:
    contracts=PATHS
    def __init__(self, *, monotonic=None, sleeper=None, governor=None):
        self.monotonic=monotonic or time.monotonic
        self.sleep=sleeper or time.sleep
        self.governor=governor or (RateGovernor(clock=monotonic,sleeper=sleeper) if monotonic or sleeper else default_governor())
        self.check_authorized = None

    def _pace(self, name, method):
        try:return self.governor.acquire(name, priority=request_priority(method))
        except RateDeferred as exc:
            raise OfficialBusinessError(exc.code,operation=name,reason=exc.reason,retry_after=exc.retry_after,origin=exc.origin,limit_kind=exc.limit_kind) from None

    @staticmethod
    def _limit_kind(code,message):
        if code not in {40100,40110}:return 'unknown'
        message=message if isinstance(message,str) else ''
        if re.search(r'天级频控|日级频控|调用天频次|当日.*(?:无法继续|超限)|daily\s+(?:quota|limit)',message,re.I):return 'day_penalty'
        if re.search(r'分钟级频控|分钟频次|分钟频控|每分钟.*(?:超限|过多)|minute\s+(?:quota|limit)',message,re.I):return 'minute_penalty'
        return 'platform_total' if code==40100 else 'developer_qps'

    @staticmethod
    def _hint(message,params,token):
        if not isinstance(message,str):return None
        hint=message
        if token:hint=hint.replace(token,'[REDACTED]')
        for key,value in params.items():
            if any(s in key.lower() for s in ('token','secret','auth_code')) and isinstance(value,str):hint=hint.replace(value,'[REDACTED]')
        hint=re.sub(r'(?i)(access_token|refresh_token|app_secret|auth_code)\s*[:=]\s*[^\s,;]+',r'\1=[REDACTED]',hint)
        hint=re.sub(r'(?i)\b[A-Za-z0-9_.-]*(?:token|secret)[A-Za-z0-9_.-]*\b','[REDACTED]',hint)
        return re.sub(r'https?://\S+','[链接略]',hint)[:240]

    def rate_status(self):return self.governor.status()

    def _retry_after(self,headers):
        value=headers.get('Retry-After','') if headers else ''
        try:
            delay=float(value) if re.fullmatch(r'[0-9]+(?:\.[0-9]+)?',value) else parsedate_to_datetime(value).timestamp()-self.governor.clock()
            return min(86400,max(0,math.ceil(delay)))
        except (TypeError,ValueError,OverflowError):return 0

    def __call__(self, name, params, token=None):
        self._require_active_license(name)
        if name not in self.contracts:
            raise ValueError('未接入该官方接口')
        host, method, path = self.contracts[name]
        headers = {'Accept': 'application/json', 'Content-Type':'application/json', 'User-Agent': 'Qianchuan-Cruise-API/1.0'}
        if token:
            headers['Access-Token'] = _secret(token)
        url = host + path
        body = None
        if method == 'POST':
            headers['Content-Type'] = 'application/json'
            body = json.dumps(params, separators=(',', ':'), allow_nan=False).encode()
        else:
            query = {k: json.dumps(v, separators=(',', ':'), ensure_ascii=False) if isinstance(v, (dict, list)) else v for k, v in params.items()}
            url += '?' + urllib.parse.urlencode(query)
        # Return quota waits to the owning scheduler so it can set a new
        # due time and release its workflow lock. Never replay this request.
        receipt=self._pace(name, method)
        if self.check_authorized:
            try:self._require_active_license(name)
            except ValueError:
                self.governor.outcome(receipt,'not_sent')
                raise
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
            with opener.open(urllib.request.Request(url, body, headers, method=method), timeout=20) as response:
                raw = response.read(4_000_001)
                retry_after=self._retry_after(getattr(response,'headers',None))
            if len(raw) > 4_000_000:raise ValueError('官方响应超过本机读取上限')
            result = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except urllib.error.HTTPError as exc:
            self.governor.outcome(receipt,'rate_limited' if exc.code==429 else 'http_error')
            self.governor.failure(name,exc.code,'rate_limited' if exc.code==429 else 'http_error',None,None,None)
            if exc.code==429:self._cooldown(name,429,'rate_limited',self._retry_after(exc.headers))
            raise ValueError('官方接口HTTP状态异常：' + str(exc.code)) from None
        except (OSError, ValueError):
            self.governor.outcome(receipt,'connection_error')
            raise ValueError('官方接口连接失败或响应格式无效') from None
        if not isinstance(result, dict) or type(result.get('code')) is not int:
            self.governor.outcome(receipt,'invalid_response')
            raise ValueError('官方响应缺少业务状态')
        if result['code'] != 0:
            message=result.get('message','')
            transient=(result['code'] in {40100,40110,51010} or isinstance(message,str) and
                result['code']==50000 and any(s in message for s in ('系统繁忙','系统错误','系统异常')))
            reason='rate_limited' if transient and result['code'] in {40100,40110} else 'service_busy' if transient else None
            kind=self._limit_kind(result['code'],message)
            self.governor.outcome(receipt,reason or 'business_error')
            hint=self._hint(message,params,token) if name not in {'exchange','refresh'} else None
            request_id=result.get('request_id')
            if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,120}',request_id) or token and token in request_id:request_id=None
            scope=json.dumps({k:params[k] for k in ('advertiser_id','adlab_scene','marketing_goal') if k in params},ensure_ascii=False)
            self.governor.failure(name,result['code'],reason,request_id,hint,scope,kind)
            if reason:
                self._cooldown(name,result['code'],reason,retry_after,hint=hint,request_id=request_id,limit_kind=kind)
            raise OfficialBusinessError(result['code'],operation=name,reason=reason,hint=hint,request_id=request_id,limit_kind=kind)
        if not isinstance(result.get('data'), dict):
            self.governor.outcome(receipt,'invalid_response')
            raise ValueError('官方响应缺少数据对象')
        self.governor.outcome(receipt,'success')
        return result['data']

    def _require_active_license(self, operation):
        if self.check_authorized:
            try:self.check_authorized()
            except ValueError as exc:
                raise OfficialBusinessError(403, operation=operation, reason='license_expired',
                                            origin='local_license', hint=str(exc)) from exc

    def _cooldown(self,name,code,reason,retry_after=0,*,hint=None,request_id=None,limit_kind=None):
        delay=self.governor.trip(name,code,reason,retry_after,limit_kind)
        raise OfficialBusinessError(code,operation=name,reason=reason,retry_after=delay,hint=hint,request_id=request_id,limit_kind=limit_kind)


class OfficialWriteTransport(OfficialTransport):
    """Instantiated by the per-action ledger, never by the read/poll client."""
    contracts=WRITE_PATHS


class OfficialAPI:
    def __init__(self, data_dir, *, transport=None, vault=None, clock=None):
        self.data_dir = Path(data_dir)
        self.vault = vault or CredentialVault(self.data_dir / 'official-api.credentials.dpapi')
        self.transport = transport or OfficialTransport(governor=RateGovernor(self.data_dir/'official-api-rate.db'))
        self.clock = clock or time.time
        self.lock = threading.RLock()
        self.read_lock = threading.RLock()
        self.metadata_lock = threading.RLock()
        self.metadata_cache = {}
        self.metadata_hits = 0
        self.last_poll = 0
        self.polling = {}
        self.poll_interval = 300
        self.cache_path = self.data_dir / 'official-api-readings.json'
        self.application_path = self.data_dir / 'official-api-application.json'
        self.authorization_result_path = self.data_dir / 'official-api-authorization-result.json'
        self.plan_cache_path = self.data_dir / 'official-api-plans.json'
        self.plan_readings = {}
        self.configuration_path=self.data_dir/'official-api-configurations.json'
        self.configuration_readings={}
        self.configuration_cache_hits=0
        self.empty_scope_path=self.data_dir/'official-api-empty-plan-scopes.json'
        self.empty_plan_scopes={}
        self.empty_plan_cache_hits=0
        if self.empty_scope_path.exists():
            try:
                value=json.loads(self.empty_scope_path.read_text('utf-8'))
                if isinstance(value,dict):self.empty_plan_scopes=value
            except (ValueError,OSError):pass
        if self.configuration_path.exists():
            try:self.configuration_readings=json.loads(self.configuration_path.read_text('utf-8'))
            except (ValueError,OSError):pass
        self.workflows = {}
        self.workflow_progress = {}
        workflow_path = self.data_dir / 'official-api-workflows.json'
        if workflow_path.exists():
            try:self.workflows = json.loads(workflow_path.read_text('utf-8'))
            except (ValueError,OSError):pass
        if self.plan_cache_path.exists():
            try:self.plan_readings = json.loads(self.plan_cache_path.read_text('utf-8'))
            except (ValueError, OSError):pass
        self.readings = {}
        if self.cache_path.exists():
            try:
                self.readings = json.loads(self.cache_path.read_text('utf-8'))
            except (ValueError, OSError):
                pass
        # A service restart should retain the normal interval of a successful
        # cached read instead of immediately repeating every account request.
        from .schema import parse_time
        for aid,p in self.plan_readings.items():
            rows=[self.readings.get(aid+':'+e['delivery_entry']) for e in p.get('entries',[])]
            if not rows or any(r is None for r in rows):continue
            try:at=min(parse_time(r['captured_at']).timestamp() for r in [p,*rows])
            except (ValueError,KeyError,TypeError):continue
            if at>self.clock()+120:continue
            self.polling[aid]={'state':'ok','last_attempt':at,'last_success':at,'next_due':at+self.poll_interval,'error':None}

    def status(self, port):
        with self.lock:
            value = self.vault.load()
            app = value['app']
            last_authorization = {}
            if self.authorization_result_path.exists():
                try:
                    last_authorization = json.loads(self.authorization_result_path.read_text('utf-8'))
                except (ValueError, OSError):
                    pass
            application = {}
            if self.application_path.exists():
                try:
                    record = json.loads(self.application_path.read_text('utf-8'))
                    if record.get('status') in {'review', 'approved'}:
                        application = {k: record[k] for k in ('name', 'status', 'checked_at', 'scope_ids')}
                except (ValueError, OSError, KeyError, AttributeError):
                    pass
            return {'configured': bool(app), 'app_id': app.get('app_id', ''),
                    'rate_control':self.rate_status(),
                    'application': application,
                    'last_authorization': last_authorization,
                    'polling':copy.deepcopy(self.polling),'poll_interval_seconds':self.poll_interval,
                    'plan_readings': copy.deepcopy(self.plan_readings),
                    'workflows': copy.deepcopy(self.workflows),
                    'secret_saved': bool(app.get('secret')), 'authorization_url': app.get('authorization_url', ''),
                    'callback_url': self.callback_url(port), 'scope_ids': app.get('scope_ids', []),
                    'grants': [{'id': gid, 'label': g['label'], 'expires_at': g['expires_at'],
                                'refresh_expires_at': g['refresh_expires_at'], 'accounts': copy.deepcopy(g.get('accounts', []))}
                               for gid, g in value['grants'].items()],
                    'readings': copy.deepcopy(self.readings), 'business_write_enabled': False,
                    'stage': (('application_review' if application['status']=='review' else 'application_approved') if application else 'awaiting_app') if not app else 'awaiting_authorization' if not value['grants'] else 'read_connection_ready'}

    def rate_status(self):
        return {**(self.transport.rate_status() if hasattr(self.transport,'rate_status') else {}),
            'metadata_cache_hits':self.metadata_hits,'configuration_cache_hits':self.configuration_cache_hits,
            'empty_plan_cache_hits':self.empty_plan_cache_hits}

    def metadata(self,key,token,load,ttl=900):
        # Cache only static schema/identity mappings. Token changes isolate
        # grants in memory; neither tokens nor their digest are persisted.
        key=(id(self.transport),hashlib.sha256((token or '').encode()).digest(),*key)
        with self.metadata_lock:
            now=self.clock();cached=self.metadata_cache.get(key)
            if cached and 0<=now-cached[0]<ttl:
                self.metadata_hits+=1
                return copy.deepcopy(cached[1])
            value=load()
            self.metadata_cache[key]=(now,copy.deepcopy(value))
            if len(self.metadata_cache)>512:
                self.metadata_cache={k:v for k,v in self.metadata_cache.items() if 0<=now-v[0]<900}
                while len(self.metadata_cache)>512:self.metadata_cache.pop(next(iter(self.metadata_cache)))
            return copy.deepcopy(value)

    @staticmethod
    def callback_url(port):
        return f'http://127.0.0.1:{port}/oauth/oceanengine/callback'

    def connected(self):
        with self.lock:return bool(self.vault.load()['grants'])

    def configure(self, payload, port):
        if not isinstance(payload, dict) or set(payload) != {'app_id', 'secret', 'authorization_url', 'scope_ids'}:
            raise ValueError('请填写应用ID、Secret、后台授权URL和明确权限编号')
        aid = exact_id(payload['app_id'])
        scopes = payload['scope_ids']
        if not isinstance(scopes, list) or not scopes or len(scopes) > 100:
            raise ValueError('请从开发者后台填写本应用实际申请的权限编号，不默认申请全部权限')
        scopes = list(dict.fromkeys(exact_id(x) for x in scopes))
        if not isinstance(payload['authorization_url'], str) or len(payload['authorization_url']) > 8192:
            raise ValueError('请填写本应用后台显示的授权URL')
        parsed = urllib.parse.urlsplit(payload['authorization_url'])
        if (parsed.scheme != 'https' or parsed.hostname not in {'ad.oceanengine.com', 'open.oceanengine.com', 'qianchuan.jinritemai.com'}
                or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment):
            raise ValueError('授权URL须来自巨量开放平台HTTPS地址')
        query = urllib.parse.parse_qs(parsed.query)
        if (query.get('app_id') != [aid] or any(len(v) != 1 for v in query.values())
                or any(k not in {'app_id', 'redirect_uri', 'scope', 'state', 'material_auth', 'rid'} for k in query)):
            raise ValueError('授权URL应用ID不一致或携带额外参数')
        qc_link = parsed.hostname == 'qianchuan.jinritemai.com'
        if qc_link and (parsed.path != '/openapi/qc/audit/oauth.html'
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', query.get('rid', [''])[0])):
            raise ValueError('请填写本应用后台显示的完整千川授权URL')
        if not qc_link and 'rid' in query:
            raise ValueError('rid仅用于千川官方应用授权地址')
        # The PC app backend issues a rid-bound callback instead of placing
        # redirect_uri in its link. Never manufacture rid or replace its host.
        if (not qc_link or 'redirect_uri' in query) and query.get('redirect_uri') != [self.callback_url(port)]:
            raise ValueError('授权URL的回调地址须与本页显示的本机回调完全一致')
        # Do not retain someone else's state from a copied example link.
        query.pop('state', None)
        clean_url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode({k: v[0] for k, v in query.items()})))
        with self.read_lock, self.lock:
            value = self.vault.load()
            secret = payload['secret']
            if not secret and value['app'].get('app_id') == aid:
                secret = value['app'].get('secret')
            app = {'app_id': aid, 'secret': _secret(secret), 'authorization_url': clean_url, 'scope_ids': scopes}
            if value['app'] != app:
                value.update(app=app, grants={}, pending={})
                self.vault.save(value)
                self.readings = {}
                self._save_readings()
                self.plan_readings = {}
                self._save_plans()
                self.configuration_readings={}
                self._save_configurations()
                self.empty_plan_scopes={}
                self._save_empty_plan_scopes()
                self.polling = {}
                self.workflows = {}
                (self.data_dir/'official-api-workflows.json').write_text('{}',encoding='utf-8')
        return {'ok': True, 'configured': True}

    def start_authorization(self, label):
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 50:
            raise ValueError('请填写本次授权的名称')
        with self.lock:
            value = self.vault.load()
            if not value['app']:
                raise ValueError('请先保存应用配置')
            state, browser_nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            value['pending'] = {hashlib.sha256(state.encode()).hexdigest(): {
                'browser_hash': hashlib.sha256(browser_nonce.encode()).hexdigest(),
                'label': label.strip(), 'expires_at': self.clock() + 600}}
            self.vault.save(value)
            parsed = urllib.parse.urlsplit(value['app']['authorization_url'])
            query = urllib.parse.parse_qs(parsed.query)
            query.update(state=[state], scope=[json.dumps([int(s) for s in value['app']['scope_ids']])], material_auth=['0'])
            url = urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode({k: v[0] for k, v in query.items()})))
            return {'authorization_url': url, 'browser_nonce': browser_nonce}

    def finish_authorization(self, query, browser_nonce):
        if not isinstance(query, dict) or any(not isinstance(v, list) or len(v) != 1 for v in query.values()):
            raise OAuthCallbackError('invalid_parameters', '授权回调参数重复或格式无效')
        # OAuth 2.0 section 4.1.2 requires ignoring unknown response fields.
        # Identity still comes only from our state, browser cookie and app ID.
        state = query.get('state', [''])[0]
        code = query.get('auth_code', [''])[0]
        if not isinstance(browser_nonce, str) or not browser_nonce:
            raise OAuthCallbackError('browser_cookie_missing', '回调未携带本机发起授权时的浏览器校验Cookie')
        if not state or not code:
            raise OAuthCallbackError('fields_missing', '官方回调缺少state或auth_code，未换取Token')
        with self.read_lock, self.lock:
            value = self.vault.load()
            key = hashlib.sha256(state.encode()).hexdigest()
            pending = value['pending'].get(key)
            if not pending:
                raise OAuthCallbackError('unknown_state', '回调不属于最新本机授权会话，未换取Token')
            if pending['expires_at'] < self.clock():
                raise OAuthCallbackError('state_expired', '本机授权会话已过期，未换取Token')
            if not hmac.compare_digest(pending['browser_hash'], hashlib.sha256(browser_nonce.encode()).hexdigest()):
                raise OAuthCallbackError('browser_cookie_mismatch', '回调浏览器校验Cookie与本机授权会话不一致')
            if query.get('app_id', [value['app']['app_id']]) != [value['app']['app_id']]:
                raise OAuthCallbackError('app_mismatch', '回调应用ID与本机配置不一致')
            del value['pending'][key]
            self.vault.save(value)  # Claim once, before the one-use code is sent.
            app = value['app']
            result = self.transport('exchange', {'app_id': int(app['app_id']), 'secret': app['secret'],
                                                'grant_type': 'auth_code', 'auth_code': _secret(code)})
            grant = self._bundle(result, pending['label'])
            gid = 'grant_' + secrets.token_hex(10)
            value['grants'][gid] = grant
            self.vault.save(value)
            return {'ok': True, 'grant_id': gid}

    def record_authorization_result(self, ok, error=None):
        from .schema import now_cn
        reason = 'connected' if ok else getattr(error, 'reason', 'local_processing_failed')
        message = '账户授权已加密保存' if ok else '本机授权处理失败，未保存连接'
        if isinstance(error, OAuthCallbackError):
            message = str(error)
        elif isinstance(error, ValueError) and re.fullmatch(r'官方接口(?:HTTP状态异常：[0-9]{3}|未成功，业务码：[0-9]+|连接失败或响应格式无效)', str(error)):
            message = str(error)
            reason = 'token_exchange_failed'
        result = {'ok': bool(ok), 'reason': reason, 'message': message, 'at': now_cn().isoformat()}
        with self.lock:
            try:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                temp = self.authorization_result_path.with_suffix('.tmp')
                temp.write_text(json.dumps(result, ensure_ascii=False), 'utf-8')
                os.replace(temp, self.authorization_result_path)
            except OSError:
                pass  # Diagnostics must never prevent the clean callback redirect.

    def _bundle(self, result, label):
        for key in ('expires_in', 'refresh_token_expires_in'):
            if type(result.get(key)) is not int or not 0 < result[key] <= 366 * 86400:
                raise ValueError('授权响应有效期无效，未保存凭据')
        accounts = []
        for aid in result.get('advertiser_ids', []):
            accounts.append({'id': exact_id(aid), 'name': '', 'type': 'token_advertiser'})
        return {'label': label, 'access_token': _secret(result.get('access_token')),
                'refresh_token': _secret(result.get('refresh_token')),
                'expires_at': self.clock() + result['expires_in'],
                'refresh_expires_at': self.clock() + result['refresh_token_expires_in'], 'accounts': accounts}

    def _token(self, gid):
        # Caller holds lock, so concurrent reads cannot rotate the same token.
        value = self.vault.load()
        if not isinstance(gid, str) or gid not in value['grants']:
            raise ValueError('请选择已完成的账户授权')
        grant = value['grants'][gid]
        if grant['expires_at'] - self.clock() > 120:
            return grant['access_token']
        if grant['refresh_expires_at'] <= self.clock():
            raise ValueError('账户授权已过期，请重新授权')
        app = value['app']
        result = self.transport('refresh', {'app_id': int(app['app_id']), 'secret': app['secret'],
                                           'grant_type': 'refresh_token', 'refresh_token': grant['refresh_token']})
        updated = self._bundle(result, grant['label'])
        updated['accounts'] = grant.get('accounts', [])
        value['grants'][gid] = updated
        self.vault.save(value)  # Persist both rotated tokens before exposing one.
        return updated['access_token']

    def discover(self, gid):
        with self.lock:
            token = self._token(gid)
            result = self.transport('accounts', {'access_token': token}, token)
            if not isinstance(result.get('list'), list):
                raise ValueError('授权账户列表结构无法识别')
            accounts = []
            for row in result['list']:
                if not isinstance(row, dict):
                    raise ValueError('授权账户列表结构无法识别')
                accounts.append({'id': exact_id(row.get('account_id', row.get('advertiser_id'))),
                                 'name': str(row.get('account_name', row.get('advertiser_name', '')))[:150],
                                 'type': str(row.get('account_type', row.get('account_role', 'unknown')))[:100]})
            value = self.vault.load()
            value['grants'][gid]['accounts'] = accounts
            self.vault.save(value)
            return {'ok': True, 'accounts': accounts}

    def _save_readings(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp = self.cache_path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.readings, ensure_ascii=False, allow_nan=False), 'utf-8')
        os.replace(temp, self.cache_path)

    def _save_plans(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temp=self.plan_cache_path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.plan_readings,ensure_ascii=False,allow_nan=False),'utf-8')
        os.replace(temp,self.plan_cache_path)

    def _save_configurations(self):
        self.data_dir.mkdir(parents=True,exist_ok=True)
        temp=self.configuration_path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.configuration_readings,ensure_ascii=False,allow_nan=False),'utf-8')
        os.replace(temp,self.configuration_path)

    def _save_empty_plan_scopes(self):
        self.data_dir.mkdir(parents=True,exist_ok=True)
        temp=self.empty_scope_path.with_suffix('.tmp')
        temp.write_text(json.dumps(self.empty_plan_scopes,ensure_ascii=False,allow_nan=False),'utf-8')
        os.replace(temp,self.empty_scope_path)

    def read_plans(self, account, gid, *, configuration=None,background=False):
        from .official_monitoring import read_plans
        return read_plans(self,account,gid,configuration=configuration,background=background)

    def scan_workflow(self, account, profile, pid, *, materials=False, progress=None, rule_metrics=False, browser_candidates=False):
        from .official_workflow import scan
        key=account['id']+':'+exact_id(pid)
        if materials:
            with self.lock:self.workflow_progress[key]={'state':'reading','page':0,'scanned_rows':0,'matched_materials':0,'identities_checked':0}
        try:
            result=scan(self,account,profile,exact_id(pid),materials=materials,progress=progress,rule_metrics=rule_metrics,browser_candidates=browser_candidates)
            if materials:
                with self.lock:self.workflow_progress[key].update(state='complete',batches=len(result['batches']))
            return result
        except Exception as exc:
            if materials:
                with self.lock:self.workflow_progress[key].update(state='error',error=str(exc)[:300])
            raise

    def _poll_recent(self,component,account,gid):
        from .schema import parse_time,now_cn
        entries=report_entries(account);day=now_cn().date().isoformat()
        if component=='account':values=[self.readings.get(account['id']+':'+e,{}) for e in entries]
        else:
            value=self.plan_readings.get(account['id'],{})
            if not value.get('complete') or {e.get('delivery_entry') for e in value.get('entries',[])}!=set(entries):return None
            values=[value]
        try:
            if any(v.get('date')!=day or v.get('grant_id')!=gid for v in values):return None
            at=min(parse_time(v['captured_at']).timestamp() for v in values)
            return at if 0<=self.clock()-at<self.poll_interval else None
        except (KeyError,ValueError,TypeError):return None

    def poll_next(self, accounts):
        """One due registered account, serialized with manual reads; no ad IO."""
        if not self.read_lock.acquire(blocking=False):return {'state':'busy','ad_changes':0}
        try:
            with self.lock:
                if not self.vault.load()['grants']:return {'state':'not_connected','ad_changes':0}
                at=self.clock()
                allowed=[a for a in accounts if a.get('observe',True) is True]
                due=[a for a in allowed if self.polling.get(a['id'],{}).get('next_due',0)<=at]
                if not due:return {'state':'idle','ad_changes':0}
                account=min(due,key=lambda a:self.polling.get(a['id'],{}).get('last_attempt',0))
                aid=account['id'];prior=self.polling.get(aid,{})
                self.polling[aid]={**prior,'state':'reading','last_attempt':at,'next_due':at+self.poll_interval,'error':None}
            try:
                gid=self.grant_for_account(aid)
                from .schema import now_cn
                day=now_cn().date().isoformat();scope=json.dumps([gid,account.get('scene'),report_entries(account)])
                components=copy.deepcopy(prior.get('components',{}))
                # Effect reports and plan discovery are independent readers.
                # A total quota on /list/ must not stop the account reports.
                for name in ('account','plans'):
                    part=components.get(name,{})
                    if part.get('scope')!=scope or part.get('date')!=day:part={}
                    if part.get('next_due',0)>self.clock():
                        components[name]=part;continue
                    recent=self._poll_recent(name,account,gid)
                    if recent is not None:
                        components[name]={'scope':scope,'date':day,'state':'ok','last_success':recent,'next_due':recent+self.poll_interval};continue
                    try:
                        if name=='account':self.read_account(account,gid)
                        else:self.read_plans(account,gid,background=True)
                        components[name]={'scope':scope,'date':day,'state':'ok','last_success':self.clock(),'next_due':self.clock()+self.poll_interval}
                    except Exception as exc:
                        limited=getattr(exc,'reason',None) in {'rate_limited','service_busy'}
                        delay=max(120 if name=='plans' else 20,getattr(exc,'retry_after',0)) if limited else 60
                        components[name]={**part,'scope':scope,'date':day,'state':'deferred' if limited else 'error',
                            'error':str(exc)[:400] if isinstance(exc,ValueError) else '官方API本次读取未完成',
                            'operation':getattr(exc,'operation',None),'origin':getattr(exc,'origin',None),
                            'limit_kind':getattr(exc,'limit_kind',None),'next_due':self.clock()+delay}
                errors=[p for p in components.values() if p['state']!='ok']
                with self.lock:
                    self.polling[aid].update(components=components,next_due=min(p['next_due'] for p in components.values()))
                    if errors:
                        p=errors[0];healthy=any(c['state']=='ok' for c in components.values())
                        self.polling[aid].update(state='partial' if healthy else p['state'],error=('账户指标已更新；' if components['account']['state']=='ok' else '')+p['error'],
                            operation=p.get('operation'),origin=p.get('origin'),limit_kind=p.get('limit_kind'))
                    else:self.polling[aid].update(state='ok',last_success=self.clock(),error=None,operation=None,origin=None,limit_kind=None)
            except ValueError as exc:
                delay=max(60,self.poll_interval,getattr(exc,'retry_after',0)) if getattr(exc,'reason',None) in {'rate_limited','service_busy'} else 60
                with self.lock:self.polling[aid].update(state='deferred' if getattr(exc,'reason',None) in {'rate_limited','service_busy'} else 'error',
                    error=str(exc)[:400],next_due=self.clock()+delay,operation=getattr(exc,'operation',None),origin=getattr(exc,'origin',None))
            except Exception:
                with self.lock:self.polling[aid].update(state='error',error='官方API本次读取未完成',next_due=self.clock()+60)
            with self.lock:return {'account_id':aid,**copy.deepcopy(self.polling[aid]),'ad_changes':0}
        finally:self.read_lock.release()

    def grant_for_account(self, aid):
        with self.lock:
            grants=self.vault.load()['grants']
            proven={r['grant_id'] for r in self.readings.values() if r.get('account_id')==aid and r.get('grant_id') in grants}
            if len(proven)==1:return next(iter(proven))
            if len(grants)==1:return next(iter(grants))
            if grants:raise ValueError('本户有多个授权连接可选，请先在API接入页选择连接并读取本户')
            return None

    def read_account(self, account, gid):
        from .schema import now_cn
        aid = exact_id(account['id'])
        selected_entry = delivery_entry(account)
        entries = report_entries(account)
        if account.get('scene') not in {'live', 'product'}:
            raise ValueError('账户场景无效')
        goal = 'LIVE_PROM_GOODS' if account['scene'] == 'live' else 'VIDEO_PROM_GOODS'
        at = now_cn()
        rows = []
        with self.read_lock:
            with self.lock:token = self._token(gid)
            for entry in entries:
                metrics, endpoint, topic, status = self._entry_report(aid, account['scene'], entry, at, token)
                rows.append({'account_id': aid, 'date': at.date().isoformat(), 'marketing_goal': goal,
                             'delivery_entry': entry, 'entry_label': ENTRIES[entry],
                             'adlab_scene': ADLAB_SCENES[entry], 'data_period': 'ALL_DATA' if entry=='chengfang' or account['scene']=='live' else None,
                             'data_topic': topic, 'data_status': status,
                             'captured_at': now_cn().isoformat(), 'data_at': None,
                             'metrics': metrics, 'endpoint': endpoint, 'grant_id': gid,
                             'note': '按投放入口分别读取；接口未返回业务更新时间。'})
            # Publish only a complete read. A failed second entry cannot appear
            # as a successful combined-account refresh or replace earlier data.
            with self.lock:
                self.readings.pop(aid, None)  # Remove the pre-entry cache format.
                for key in list(self.readings):
                    if key.startswith(aid + ':'):
                        del self.readings[key]
                for row in rows:
                    self.readings[aid + ':' + row['delivery_entry']] = row
                self._save_readings()
        return {'ok': True, 'delivery_entry': selected_entry,
                'reading': copy.deepcopy(rows[0]) if len(rows) == 1 else None,
                'readings': copy.deepcopy(rows), 'ad_changes': 0}

    def _entry_report(self, aid, scene, entry, at, token):
        start, end = at.strftime('%Y-%m-%d 00:00:00'), at.strftime('%Y-%m-%d %H:%M:%S')
        if entry == 'uni_promotion' and scene == 'live':
            mapping = ACCOUNT_FIELDS
            result={}
            for fields in LIVE_FIELD_GROUPS:
                part=self.transport('account_report', {'advertiser_id':int(aid), 'start_time':start,
                    'end_time':end, 'marketing_goal':'LIVE_PROM_GOODS', 'order_platform':'QIANCHUAN', 'adlab_scene':'UNI_PROJECT',
                    'data_period':'ALL_DATA','fields':list(fields)}, token)
                result.update({field:part.get(field) for field in fields})
            if not any(result.get(k) not in (None,'-') for k in mapping):
                return {target:None for target in mapping.values()},PATHS['account_report'][2],None,'no_rows'
            return self._account_metrics(result,mapping), PATHS['account_report'][2], None, 'observed'
        topic = ('OVERALL_ROI_LIVE_AWEME' if scene=='live' else 'OVERALL_ROI_PRODUCT_AWEME') if entry=='chengfang' else 'SITE_PROMOTION_PRODUCT_AD'
        from .official_monitoring import config
        schema=config(self,aid,topic,entry,token)
        dimensions = schema.get('dimensions',[])
        if any(d.get('is_required') or d.get('filter_config',{}).get('filter_required') for d in dimensions):
            raise ValueError('本入口要求额外维度或过滤条件，不能冒充账户汇总')
        available = {m.get('field') for m in schema.get('metrics',[]) if isinstance(m,dict)}
        mapping = {k:v for k,v in ACCOUNT_FIELDS.items() if k!='stat_cost_for_roi2' and k in available}
        spend_field = 'stat_cost' if 'stat_cost' in available else 'stat_cost_for_roi2'
        if spend_field in available:mapping[spend_field]='spend'
        if not mapping:raise ValueError('本入口没有已核验的账户汇总指标')
        query = {'advertiser_id':int(aid), 'data_topic':topic, 'dimensions':[], 'metrics':list(mapping),
                 'filters':[], 'order_by':[], 'start_time':start, 'end_time':end, 'page':1, 'page_size':10}
        if entry=='chengfang':query['data_period']='ALL_DATA'
        result = self.transport('report_data',query,token)
        rows = result.get('rows');info = result.get('page_info',{})
        if rows==[] and info.get('total_number')==0:
            return {target:None for target in ACCOUNT_FIELDS.values()},PATHS['report_data'][2],topic,'no_rows'
        if (not isinstance(rows,list) or len(rows)!=1 or info.get('total_number')!=1
                or rows[0].get('dimensions')!={} or not isinstance(rows[0].get('metrics'),dict)):
            raise ValueError('本入口未返回唯一的无维度汇总行，未合并或平均ROI')
        values = {}
        for field in mapping:
            cell = rows[0]['metrics'].get(field)
            if isinstance(cell,dict):
                if 'Value' not in cell:raise ValueError('官方报表指标缺少原生Value，未解析格式化显示文本')
                cell = cell['Value']
            values[field] = cell
        return self._account_metrics(values,mapping),PATHS['report_data'][2],topic,'observed'

    @staticmethod
    def _account_metrics(result, mapping=None):
        # Select only known numeric business fields. Unknown/missing stays null.
        metrics = {target:None for target in ACCOUNT_FIELDS.values()}
        for field, target in (mapping or ACCOUNT_FIELDS).items():
            raw = result.get(field)
            if raw is None or raw == '-':
                metrics[target] = None
            elif type(raw) in (int, float) or isinstance(raw, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', raw):
                number = float(raw)
                if number < 0 or not math.isfinite(number):
                    raise ValueError('官方账户指标无效')
                metrics[target] = number
            else:
                raise ValueError('官方账户指标格式无法识别')
        if not any(v is not None for v in metrics.values()):
            raise ValueError('官方账户响应无可识别指标，未填零')
        return metrics
