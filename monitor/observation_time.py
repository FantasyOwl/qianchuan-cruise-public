"""Review DOM clock scope without rewriting stored observations.

Overview clocks do not establish when a plan table was updated. A detail-page
header also stayed at 09:16 after refresh changed the visible task counters.
"""
from urllib.parse import urlsplit

PLAN_CLOCK_ISSUE = '计划行没有独立指标更新时间，账户概览时间不能代替计划时间'
DETAIL_CLOCK_ISSUE = '直播计划详情顶部时间不能认证明细独立指标更新时间'
OVERVIEW_PATHS = {'/overall-prom', '/uni-prom'}
DETAIL_PATH = '/uni-prom/detail'
DETAIL_SCOPES = {'account', 'plan', 'control'}


def review_observation_time(snapshot):
    if not isinstance(snapshot, dict):
        return snapshot
    source = snapshot.get('source')
    if not isinstance(source, dict) or source.get('kind') != 'browser_dom':
        return snapshot
    try:
        page = urlsplit(source.get('url', ''))
    except (ValueError, TypeError, AttributeError):
        return snapshot
    if page.hostname != 'qianchuan.jinritemai.com':
        return snapshot
    if page.path in OVERVIEW_PATHS and snapshot.get('scope') == 'plan':
        issue, reported_field = PLAN_CLOCK_ISSUE, 'reported_overview_data_at'
    elif page.path == DETAIL_PATH and snapshot.get('scope') in DETAIL_SCOPES:
        issue, reported_field = DETAIL_CLOCK_ISSUE, 'reported_detail_header_data_at'
    else:
        return snapshot
    # This is a read projection for historical rows. Their IDs still refer to
    # the original stored evidence; no SQL UPDATE or new capture is performed.
    reviewed_source = {**source, 'data_at': None}
    if source.get('data_at') is not None:
        reviewed_source[reported_field] = source['data_at']
    quality = snapshot.get('quality')
    quality = quality if isinstance(quality, dict) else {}
    supplied = quality.get('issues', [])
    issues = list(supplied) if isinstance(supplied, list) else []
    if issue not in issues:
        issues.append(issue)
    return {**snapshot, 'source': reviewed_source,
            'quality': {**quality, 'issues': issues}}
