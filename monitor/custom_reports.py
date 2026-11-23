"""Lossless, local review of supplied native custom-report packages.

The public contract defines cells as objects, without defining their members.
Keep those objects intact; never guess a value member or a normalized metric.
This module has no network, storage, AI or execution dependency.
"""
from decimal import Decimal
import hashlib
import json
import re

from .official_reports import _guard, reject_duplicate_keys
from .schema import now_cn, parse_time

FORMAT = 'qianchuan_custom_report_review_v1'
CONFIG_ENDPOINT = '/open_api/v1.0/qianchuan/report/uni_promotion/config/get/'
DATA_ENDPOINT = '/open_api/v1.0/qianchuan/report/uni_promotion/data/get/'
REFERENCES = [
    'https://open.oceanengine.com/labels/12/docs/1823296280645708',
    'https://open.oceanengine.com/labels/12/docs/1823297941140569',
]
TOPICS = {
    'SITE_PROMOTION_PRODUCT_AD': ('product', '商品全域 · 计划'),
    'SITE_PROMOTION_PRODUCT_PRODUCT': ('product', '商品全域 · 商品'),
    'SITE_PROMOTION_PRODUCT_POST_ASSIST_TASK': ('product', '商品全域 · 调控任务'),
    'SITE_PROMOTION_POST_DATA_LIVE': ('live', '直播全域 · 直播间画面'),
    'SITE_PROMOTION_POST_DATA_VIDEO': ('live', '直播全域 · 视频'),
    'SITE_PROMOTION_POST_DATA_OTHER': ('live', '直播全域 · 其他创意'),
    'SITE_PROMOTION_POST_DATA_TITLE': ('live', '直播全域 · 标题'),
    'SITE_PROMOTION_PRODUCT_POST_DATA_TITLE': ('product', '商品全域 · 标题'),
    'SITE_PROMOTION_PRODUCT_POST_DATA_IMAGE': ('product', '商品全域 · 图片'),
    'SITE_PROMOTION_PRODUCT_POST_DATA_VIDEO': ('product', '商品全域 · 视频'),
    'SITE_PROMOTION_PRODUCT_POST_DATA_OTHER': ('product', '商品全域 · 其他创意'),
    'ROI2_IMAGE_AGG_MATERIAL_ANALYSIS': (None, '全域 · 图文素材'),
    'OVERALL_ROI_PRODUCT_PROJECT': ('product', '乘方商品 · 计划'),
    'OVERALL_ROI_PRODUCT_AWEME': ('product', '乘方商品 · 抖音号'),
    'OVERALL_ROI_PRODUCT_PRODUCT': ('product', '乘方商品 · 商品'),
    'OVERALL_ROI_PRODUCT_MATERIAL': ('product', '乘方商品 · 素材'),
    'OVERALL_ROI_LIVE_AWEME': ('live', '乘方直播 · 抖音号'),
    'OVERALL_ROI_LIVE_MATERIAL_LIVE': ('live', '乘方直播 · 直播间画面'),
    'OVERALL_ROI_LIVE_MATERIAL_VIDEO': ('live', '乘方直播 · 素材'),
}
UNITS = {0: '无单位', 1: '千分之一分', 2: '分', 3: '元', 4: '个', 10: '百分比'}
LIMITS = [
    '查询上下文、配置和响应均由文件提供，未认证平台来源或账户权限。',
    '原生单元格按JSON文本保留，不猜测value等成员，不换算单位或构造ROI分子。',
    '分页数量吻合只说明所提供的查询结果内部一致，不代表全账户、完整素材清单或同一平台快照。',
    '采集时间和查询结束时间均不替代独立数据更新时间。',
    '此核对不保存观察快照、不接入调控或AI；输出只包含选定字段，未返回值保持缺失。',
    '查询日期等动态业务限制仍需按配置人工核对，此功能不认证请求可在平台执行。',
]


def _exact(obj, required, optional=()):
    if not isinstance(obj, dict) or not set(required) <= set(obj) or set(obj) - set(required) - set(optional):
        raise ValueError('明细包结构缺失或包含不支持的字段')


def _text(value, limit=500):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('报表字段或说明文本无效')
    return value


def _fields(value):
    if not isinstance(value, list) or not 1 <= len(value) <= 100:
        raise ValueError('维度和指标列表须各包含1至100个字段（本地限制）')
    for key in value:
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,127}', key):
            raise ValueError('报表字段标识无效')
    if len(set(value)) != len(value):
        raise ValueError('报表字段重复')
    return value


def _id(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value) or int(value) > 2**64-1:
        raise ValueError('账户ID必须为无损的十进制整数或字符串')
    return value


def _json(value, depth=0):
    """JSON text with arbitrary-size integers and decimal lexical precision."""
    if depth > 8:
        raise ValueError('原生单元格嵌套过深')
    if isinstance(value, dict):
        if len(value) > 100:
            raise ValueError('原生单元格字段过多')
        return '{' + ','.join(json.dumps(k, ensure_ascii=False)+':'+_json(v, depth+1)
                              for k, v in sorted(value.items())) + '}'
    if isinstance(value, list):
        if len(value) > 100:
            raise ValueError('原生单元格列表过长')
        return '[' + ','.join(_json(v, depth+1) for v in value) + ']'
    if isinstance(value, Decimal):
        if not value.is_finite() or abs(value.adjusted()) > 100:
            raise ValueError('原生单元格数值超出本地范围')
        return str(value)
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _response(value):
    if not isinstance(value, dict) or type(value.get('code')) is not int or value['code'] != 0 or not isinstance(value.get('data'), dict):
        raise ValueError('必须提供明确code=0的业务响应及data对象')
    return value['data']


def review_custom_reports(payload, settings, received_at=None):
    """Validate a supplied package and return a bounded, sanitized review only."""
    received_at = received_at or now_cn()
    if isinstance(payload, dict) and set(payload) == {'document_text'}:
        raw = payload['document_text']
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > 2*1024*1024:
            raise ValueError('明细包须为不超过2MB的JSON文本')
        try:
            payload = json.loads(raw, object_pairs_hook=reject_duplicate_keys, parse_float=Decimal,
                                 parse_constant=lambda _: (_ for _ in ()).throw(ValueError('不接受非有限数字')))
        except (json.JSONDecodeError, RecursionError):
            raise ValueError('明细包不是有效JSON') from None
    _guard(payload)
    _exact(payload, {'format', 'configuration', 'query', 'pages', 'captured_at', 'data_at'})
    if payload['format'] != FORMAT:
        raise ValueError('请使用全域/乘方报表明细核对模板')
    query = payload['query']
    _exact(query, {'advertiser_id', 'data_topic', 'dimensions', 'metrics', 'filters', 'order_by',
                   'start_time', 'end_time', 'page_size'}, {'data_period'})
    account_id = _id(query['advertiser_id'])
    accounts = [a for a in settings.get('accounts', []) if a.get('id') == account_id and a.get('observe', True)]
    if len(accounts) != 1:
        raise ValueError('明细包账户不在当前启用的账户范围内')
    topic = query['data_topic']
    if not isinstance(topic, str) or topic not in TOPICS:
        raise ValueError('数据主题尚未核验')
    scene, title = TOPICS[topic]
    if scene and accounts[0]['scene'] != scene:
        raise ValueError('数据主题与账户场景不一致')
    overall = topic.startswith('OVERALL_')
    period = query.get('data_period')
    if period is not None and (not isinstance(period, str) or not overall or period not in {'ALL_DATA', 'OVER_ALL_DATA', 'UNI_DATA'}):
        raise ValueError('只有乘方主题可显式提供有效data_period')
    dims, metrics = _fields(query['dimensions']), _fields(query['metrics'])
    if set(dims) & set(metrics):
        raise ValueError('维度与指标字段不能重名')
    size = query['page_size']
    if type(size) is not int or size not in {10, 20, 50, 100, 200}:
        raise ValueError('页大小必须为10、20、50、100或200')
    for value in (query['start_time'], query['end_time'], payload['captured_at']):
        _text(value, 80)
    if payload['data_at'] is not None:
        _text(payload['data_at'], 80)
    start, end, captured = [parse_time(query[k] if k != 'captured_at' else payload[k])
                            for k in ('start_time', 'end_time', 'captured_at')]
    if start > end or start > captured or (captured-received_at).total_seconds() > 300:
        raise ValueError('查询或采集时刻顺序无效')
    data_at = parse_time(payload['data_at']) if payload['data_at'] is not None else None
    if data_at and (not start <= data_at <= end or (data_at-captured).total_seconds() > 300):
        raise ValueError('声明数据时刻与查询或采集时刻矛盾')

    cfg = payload['configuration']
    _exact(cfg, {'endpoint', 'query', 'response'})
    if cfg['endpoint'] != CONFIG_ENDPOINT:
        raise ValueError('维度配置接口不匹配')
    cq = cfg['query']
    _exact(cq, {'advertiser_id', 'data_topics'}, {'data_period'})
    if _id(cq['advertiser_id']) != account_id or cq['data_topics'] != [topic] or cq.get('data_period') != period:
        raise ValueError('配置查询与明细查询的账户、主题或周期不一致')
    configs = _response(cfg['response']).get('custom_config_datas')
    if not isinstance(configs, list) or len(configs) != 1 or not isinstance(configs[0], dict) or configs[0].get('data_topic') != topic:
        raise ValueError('请提供唯一匹配数据主题的原生配置结果')
    config = configs[0]
    maps = []
    for key in ('dimensions', 'metrics'):
        rows = config.get(key)
        if not isinstance(rows, list) or len(rows) > 2000:
            raise ValueError('配置字段列表缺失或超出本地限制')
        mapping = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('配置字段不是对象')
            field = _fields([row.get('field')])[0]
            if field in mapping:
                raise ValueError('配置字段重复，不能选择有利定义')
            _text(row.get('name'))
            for flag in ('is_required', 'filter_only', 'filterable', 'sortable'):
                if flag in row and type(row[flag]) is not bool:
                    raise ValueError('配置布尔标记无效')
            if 'filter_config' in row and not isinstance(row['filter_config'], dict):
                raise ValueError('配置筛选结构无效')
            mapping[field] = row
        maps.append(mapping)
    dm, mm = maps
    if not set(dims) <= set(dm) or not set(metrics) <= set(mm):
        raise ValueError('所选字段不在对应主题的配置中')
    for name, d in dm.items():
        if d.get('is_required') is True and name not in dims:
            raise ValueError('查询缺少配置声明的必选维度')
        if name in dims and d.get('filter_only') is True:
            raise ValueError('仅筛选字段不能作为查询维度')
    for name in dims+metrics:
        row = (dm if name in dims else mm)[name]
        for key, selected in [('exclusion_dims', dims), ('exclusion_metrics', metrics)]:
            excluded = row.get(key, [])
            if not isinstance(excluded, list) or any(not isinstance(s, str) for s in excluded):
                raise ValueError('配置互斥字段列表无效')
            if set(excluded) & set(selected):
                raise ValueError('所选维度或指标违反配置互斥关系')
    columns = [{'field': name, 'name': (dm if name in dims else mm)[name]['name'],
                'kind': 'dimension' if name in dims else 'metric',
                'unit_code': None if name in dims else mm[name].get('unit')}
               for name in dims+metrics]
    for c in columns:
        if c['kind'] == 'metric' and (type(c['unit_code']) is not int or c['unit_code'] not in UNITS):
            raise ValueError('指标单位未知，不能猜测金额或百分比')
        c['unit'] = '维度原值' if c['kind'] == 'dimension' else UNITS[c['unit_code']]

    filters, ordering = query['filters'], query['order_by']
    ql = config.get('query_limit', {})
    if not isinstance(ql, dict):
        raise ValueError('查询限制结构无效')
    query_limits = {}
    for key in ('max_dimension_limit', 'max_day', 'earliest_day', 'max_day_by_day', 'max_day_by_hour'):
        if key in ql:
            if not isinstance(ql[key], str) or len(ql[key]) > 80:
                raise ValueError('查询限制须保留原生文本')
            query_limits[key] = ql[key]
    cap = query_limits.get('max_dimension_limit')
    if cap:
        if not cap.isascii() or not cap.isdecimal() or int(cap) < 1 or len(dims) > int(cap):
            raise ValueError('查询维度数超过配置限制或限制无效')
    if not isinstance(filters, list) or len(filters) > 100 or not isinstance(ordering, list) or len(ordering) > 100:
        raise ValueError('筛选或排序结构无效')
    seen = set()
    for f in filters:
        _exact(f, {'field', 'operator', 'values'})
        name = f['field']
        if not isinstance(name, str) or name not in dm or name in seen or dm[name].get('filterable') is not True:
            raise ValueError('筛选字段未许可或重复')
        fc = dm[name].get('filter_config', {})
        if f['operator'] != 7 or type(f['operator']) is not int or fc.get('operator') != 7:
            raise ValueError('当前仅核对公开数据合同定义的包含筛选')
        values = f['values']
        if not isinstance(values, list) or not values or len(values) > 100:
            raise ValueError('筛选值缺失、重复或过多')
        for v in values:
            _text(v)
        if len(set(values)) != len(values):
            raise ValueError('筛选值重复')
        cap = fc.get('value_limit')
        if cap is not None and (type(cap) is not int or len(values) > cap):
            raise ValueError('筛选值超过配置数量限制')
        seen.add(name)
    if any(d.get('filter_config', {}).get('filter_required') is True and k not in seen for k, d in dm.items()):
        raise ValueError('缺少配置声明的必填筛选')
    seen_order = set()
    for o in ordering:
        _exact(o, {'field', 'type'})
        if not isinstance(o['field'], str) or o['field'] not in dims+metrics or o['field'] in seen_order or type(o['type']) is not int or o['type'] not in {1, 2}:
            raise ValueError('排序字段或方向无效')
        if o['field'] in dm and dm[o['field']].get('sortable') is not True:
            raise ValueError('该维度不允许排序')
        seen_order.add(o['field'])

    pages = payload['pages']
    if not isinstance(pages, list) or not 1 <= len(pages) <= 20:
        raise ValueError('每次核对1至20页（本地限制）')
    records, identities, reported = [], set(), None
    missing = 0
    for expected, page in enumerate(pages, 1):
        _exact(page, {'endpoint', 'page', 'response'})
        if page['endpoint'] != DATA_ENDPOINT or type(page['page']) is not int or page['page'] != expected:
            raise ValueError('明细接口或连续页序不匹配，须从第1页开始')
        data = _response(page['response'])
        pi, rows = data.get('page_info'), data.get('rows')
        _exact(pi, {'page', 'page_size', 'total_page', 'total_number'})
        if any(type(v) is not int or v < 0 for v in pi.values()) or pi['page'] != expected or pi['page_size'] != size:
            raise ValueError('分页元数据无效或与查询不一致')
        total, count = pi['total_page'], pi['total_number']
        if (total not in ({0, 1} if count == 0 else {(count+size-1)//size})
                or expected > max(total, 1)):
            raise ValueError('分页总数与行数矛盾')
        if reported is not None and reported != (total, count):
            raise ValueError('分页期间总数变化，不能合并为同一结果')
        reported = total, count
        if not isinstance(rows, list) or len(rows) != min(size, max(0, count-(expected-1)*size)):
            raise ValueError('页面实际行数与分页声明矛盾')
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('dimensions'), dict) or not isinstance(row.get('metrics'), dict):
                raise ValueError('请提供原生dimensions/metrics对象，不能使用扁平账户或计划列表')
            if set(row['dimensions']) != set(dims) or not set(row['metrics']) <= set(metrics):
                raise ValueError('行字段与明确查询字段不一致')
            cells = {}
            for c in columns:
                raw = row['dimensions' if c['kind'] == 'dimension' else 'metrics'].get(c['field'])
                if raw is None and c['kind'] == 'metric':
                    cells[c['field']] = None
                    missing += 1
                    continue
                if not isinstance(raw, dict) or not raw:
                    raise ValueError('原生单元格应为非空对象；不猜测或包裹标量')
                text = _json(raw)
                if len(text) > 4000:
                    raise ValueError('单元格超出本地长度限制')
                cells[c['field']] = text
            identity = tuple(cells[d] for d in sorted(dims))
            if identity in identities:
                raise ValueError('分页存在重复维度组合，不能累加或选取其中一条')
            identities.add(identity)
            records.append({'page': expected, 'cells': cells})
    normalized_query = {**query, 'advertiser_id': account_id}
    limits = list(LIMITS)
    exhausted = len(pages) == max(reported[0], 1)
    if not exhausted:
        limits.append('提供的页面尚未读完；保留现有行用于核对，不推断缺少对象没有消耗或已关闭。')
    out = {'format': FORMAT, 'account_id': account_id, 'topic_label': title, 'query': normalized_query,
           'captured_at': captured.isoformat(), 'data_at': data_at.isoformat() if data_at else None,
           'columns': columns, 'rows': records, 'query_limits': query_limits,
           'received_pages': len(pages), 'reported_pages': reported[0],
           'reported_rows': reported[1], 'received_rows': len(records), 'missing_cells': missing,
           'pagination_exhausted': exhausted, 'inventory_complete': False, 'platform_verified': False,
           'normalized_snapshots': 0, 'persisted': False, 'executed': False, 'ai_sent': False,
           'references': REFERENCES, 'limits': limits}
    out['review_digest'] = hashlib.sha256(_json(out).encode('utf-8')).hexdigest()
    return out
