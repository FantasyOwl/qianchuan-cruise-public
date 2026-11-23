"""Optional adult audience settings; absent/unlimited preserves old digests."""
AGES=('18-23','24-30','31-40','41-49','50+')
DEFAULT={'gender':'any','ages':[],'exclude_fans':False}

def validate(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT):raise ValueError('定向仅支持性别、年龄、抖音号粉丝')
    out={**DEFAULT,**value}
    if out['gender'] not in {'any','male','female'} or type(out['exclude_fans']) is not bool:raise ValueError('定向性别或粉丝选项无效')
    ages=out['ages']
    if not isinstance(ages,list) or any(not isinstance(a,str) or a not in AGES for a in ages) or len(ages)!=len(set(ages)):raise ValueError('请选择平台支持的18岁以上年龄段，可多选')
    out['ages']=[a for a in AGES if a in ages]
    return out

def effective(rule):
    return validate(rule.get('audience',{})) if rule.get('chase_type','控成本追投')=='控成本追投' else dict(DEFAULT)

def configured(rule):return effective(rule)!=DEFAULT

def check_api_fields(raw,expected):
    """Verify fields actually returned; never claim absent fan fields verified."""
    if not isinstance(raw,dict):return {}
    checked={}
    if 'gender' in raw:
        mapped={'NONE':'any','GENDER_MALE':'male','GENDER_FEMALE':'female'}.get(raw['gender'])
        if mapped!=expected['gender']:raise ValueError('API原任务定向性别不一致')
        checked['gender']=True
    if 'age' in raw:
        mapping={'AGE_BETWEEN_'+a.replace('-','_'):a for a in AGES if a!='50+'};mapping['AGE_ABOVE_50']='50+'
        if not isinstance(raw['age'],list) or any(a not in mapping for a in raw['age']) or {mapping[a] for a in raw['age']}!=set(expected['ages']):raise ValueError('API原任务定向年龄不一致')
        checked['ages']=True
    return checked
