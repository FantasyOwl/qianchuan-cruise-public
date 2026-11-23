"""Explicit API plan families. A combined account keeps separate report rows."""

ENTRIES = {'uni_promotion': '全域', 'chengfang': '乘方', 'both': '全域与乘方'}
ADLAB_SCENES = {'uni_promotion': 'UNI_PROJECT', 'chengfang': 'OVERALL_PROJECT'}


def delivery_entry(account):
    value = account.get('delivery_entry', 'uni_promotion')
    if not isinstance(value, str) or value not in ENTRIES:
        raise ValueError('API投放入口须为全域、乘方或全域与乘方')
    return value


def report_entries(account):
    value = delivery_entry(account)
    return tuple(ADLAB_SCENES) if value == 'both' else (value,)
