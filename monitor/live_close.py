"""One-time, preconfirmed native live-room slowdown schedules. No platform IO."""
import json
import secrets
import re
from decimal import Decimal
from datetime import timedelta
from urllib.parse import urlsplit,parse_qs
from .browser_actions import BrowserActions,FRESH_SECONDS,CLAIM_SECONDS,_shape,_id,_time,_dump,_hash

REVIEW_SECONDS=900
LATE_SECONDS=120

def home_url(value,aid):
    if not isinstance(value,str) or len(value)>12000:raise ValueError('首页地址无效')
    u=urlsplit(value);q=parse_qs(u.query)
    if u.scheme!='https' or u.netloc!='qianchuan.jinritemai.com' or u.path!='/uni-prom' or q.get('aavid')!=[aid]:raise ValueError('排程仅使用本账户已核验的直播首页')
    return value

def money(value):
    from decimal import Decimal,InvalidOperation
    try:n=Decimal(str(value))
    except (ValueError,InvalidOperation):raise ValueError('控量预算无效') from None
    if isinstance(value,bool) or not n.is_finite() or n<=0 or n>100000000 or n.as_tuple().exponent < -2:raise ValueError('控量预算须为正数，最多两位小数')
    return format(n.quantize(Decimal('.01')),'f')

def normalize(value,policy):
    base='account_id plan_id group path home_url tab_id document_id captured_at parent_budget running busy control_mode control_budget control_minutes control_created_at'
    extra='aweme_id detail_url parent_captured_at'
    _shape(value,base+' '+extra if isinstance(value,dict) and any(k in value for k in extra.split()) else base)
    v=dict(value);_id(v['account_id']);_id(v['plan_id']);_id(v['document_id'],opaque=True)
    for k in extra.split():v.setdefault(k,None)
    account=next((a for a in policy['accounts'] if a['id']==v['account_id'] and a['scene']=='live' and a.get('observe',True)),None)
    if not account or account['tab_group']!=v['group'] or v['path']!='/uni-prom':raise ValueError('直播账户或分组不匹配')
    home_url(v['home_url'],v['account_id']);v['parent_budget']=money(v['parent_budget']);_time(v['captured_at'])
    if type(v['tab_id']) is not int or v['tab_id']<=0 or type(v['running']) is not bool or type(v['busy']) is not bool:raise ValueError('原生计划状态未明确')
    if v['control_mode'] not in ('none','boost','control','unknown'):raise ValueError('调速类型无效')
    if v['control_budget'] is not None:v['control_budget']=money(v['control_budget'])
    if v['control_minutes'] is not None and (type(v['control_minutes']) is not int or not 1<=v['control_minutes']<=1440):raise ValueError('调速时长无效')
    if v['control_created_at'] is not None:_time(v['control_created_at'])
    if any(v[k] is not None for k in extra.split()):
        _id(v['aweme_id']);_time(v['parent_captured_at']);detail_url(v['detail_url'],v['account_id'],v['plan_id'])
    return v

def detail_url(value,aid,pid):
    if not isinstance(value,str) or len(value)>16000:raise ValueError('计划详情地址无效')
    u=urlsplit(value);q=parse_qs(u.query)
    if u.scheme!='https' or u.netloc!='qianchuan.jinritemai.com' or u.path!='/uni-prom/detail' or q.get('aavid')!=[aid] or q.get('adId')!=[pid]:raise ValueError('计划与抖音号映射地址不匹配')
    return value

class LiveCloseSchedules(BrowserActions):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        with self._tx() as db:db.execute('create table if not exists live_close_checks(worker_id text primary key,data_json text)')

    def _sweep(self,db):
        now=self._now()
        for r in db.execute("select * from browser_actions where state in ('draft','approved','claimed','submitting')").fetchall():
            state=None
            if r['state']=='draft' and _time(r['expires_at'])<=now:state='expired'
            elif r['state']=='approved' and _time(r['expires_at'])<=now:state='missed'
            elif r['claimed_at'] and (now-_time(r['claimed_at'])).total_seconds()>CLAIM_SECONDS:state='unknown'
            if state:db.execute('update browser_actions set state=? where id=?',(state,r['id']));self._event(db,r['id'],state)

    def _worker(self,db,worker_id,epoch,policy,armed=True):
        w=db.execute('select * from browser_workers where id=?',(worker_id,)).fetchone()
        if not w or w['epoch']!=epoch or w['revision']!=policy['revision'] or armed and not w['armed'] or not 0<=(self._now()-_time(w['seen_at'])).total_seconds()<=FRESH_SECONDS:raise ValueError('执行会话未就绪，等待原Chrome上报')
        return w

    def observe(self,payload,policy):
        if not isinstance(payload,dict):raise ValueError('调速观察格式无效')
        payload=dict(payload);checks=payload.pop('view_checks',[])
        _shape(payload,'protocol worker_id epoch revision armed surfaces')
        if not isinstance(checks,list) or len(checks)>100 or len(_dump(checks))>100000:raise ValueError('首页核验信息过大')
        allowed={a['id'] for a in policy['accounts'] if a['scene']=='live' and a.get('observe',True)}
        for c in checks:
            _shape(c,'account_id tab_id code detail')
            if c['account_id'] not in allowed or type(c['tab_id']) is not int or c['tab_id']<=0 or not isinstance(c['code'],str) or not re.fullmatch('[a-z_]{1,80}',c['code']) or not isinstance(c['detail'],dict) or len(_dump(c['detail']))>20000:raise ValueError('首页核验信息无效')
        if payload['protocol']!=1 or type(payload['armed']) is not bool or payload['revision']!=policy['revision']:raise ValueError('调速协议或读取范围变化')
        _id(payload['worker_id'],opaque=True);_id(payload['epoch'],opaque=True)
        if not isinstance(payload['surfaces'],list) or len(payload['surfaces'])>100:raise ValueError('调速页面清单无效')
        surfaces=[normalize(v,policy) for v in payload['surfaces']]
        if len({(v['account_id'],v['plan_id']) for v in surfaces})!=len(surfaces):raise ValueError('直播计划页面不唯一')
        if any(not 0<=(self._now()-_time(v['captured_at'])).total_seconds()<=FRESH_SECONDS for v in surfaces):raise ValueError('调速观察过期')
        if any(s['aweme_id'] and not 0<=(self._now()-_time(s['parent_captured_at'])).total_seconds()<=600 for s in surfaces):raise ValueError('计划与抖音号对应关系需重新读取详情')
        with self._tx() as db:
            self._sweep(db)
            db.execute('insert or replace into browser_workers values(?,?,?,?,?,?)',(payload['worker_id'],payload['epoch'],int(payload['armed']),policy['revision'],self._now().isoformat(),_dump(surfaces)))
            db.execute('insert or replace into live_close_checks values(?,?)',(payload['worker_id'],_dump(checks)))
            alarms=[{'id':r['id'],'at':json.loads(r['intent'])['trigger_at']} for r in db.execute("select * from browser_actions where worker_id=? and state='approved'",(payload['worker_id'],))]
        return {'ok':True,'alarms':alarms}

    def _account(self,v,policy):
        if not any(a['id']==v['account_id'] and a['scene']=='live' and a.get('observe',True) and a['tab_group']==v['group'] for a in policy['accounts']):raise ValueError('排程账户或分组已变化')

    def create(self,payload,policy):
        _shape(payload,'worker_id epoch account_id plan_id off_at budget duration_minutes')
        budget=money(payload['budget']);off=_time(payload['off_at']);start=off-timedelta(minutes=20)
        if type(payload['duration_minutes']) is not int or payload['duration_minutes'] not in (30,60):raise ValueError('控量时长请选择30或60分钟')
        if not self._now()+timedelta(minutes=2)<=start<=self._now()+timedelta(days=7):raise ValueError('控量开始时间须在2分钟至7天以后，请填写具体下播日期和时间')
        with self._tx() as db:
            self._sweep(db);w=self._worker(db,payload['worker_id'],payload['epoch'],policy)
            sources=[normalize(s,policy) for s in json.loads(w['surfaces']) if s['account_id']==payload['account_id'] and s['plan_id']==payload['plan_id'] and 0<=(self._now()-_time(s['captured_at'])).total_seconds()<=FRESH_SECONDS]
            if len(sources)!=1:raise ValueError('请在原Chrome打开本户直播首页，等待读取明确计划和一键调速入口')
            s=sources[0]
            if s['aweme_id'] and not 0<=(self._now()-_time(s['parent_captured_at'])).total_seconds()<=600:raise ValueError('请先重新读取本计划与抖音号对应关系')
            if Decimal(budget)>Decimal(s['parent_budget']):raise ValueError('控量预算不能超过当前母计划日预算')
            for old in db.execute("select intent from browser_actions where state in ('draft','approved','claimed','submitting','unknown')"):
                v=json.loads(old['intent'])
                if (v['account_id'],v['plan_id'])==(s['account_id'],s['plan_id']):raise ValueError('本计划已有待处理的下播控量排程')
            v={'target_kind':'live_close','action':'one_click_control','account_id':s['account_id'],'plan_id':s['plan_id'],'group':s['group'],'home_url':s['home_url'],
                'off_at':off.isoformat(),'trigger_at':start.isoformat(),'lead_minutes':20,'budget':budget,'duration_minutes':payload['duration_minutes'],'conflict_policy':'block_and_notify'}
            if s['aweme_id']:v.update(aweme_id=s['aweme_id'],detail_url=s['detail_url'])
            action_id=secrets.token_hex(16);now=self._now()
            db.execute('insert into browser_actions values(?,?,?,?,?,?,?,?,?,?,?,?)',(action_id,'draft',now.isoformat(),(now+timedelta(seconds=REVIEW_SECONDS)).isoformat(),w['id'],w['epoch'],policy['revision'],_dump(v),_hash(v),None,None,None))
            self._event(db,action_id,'draft');return self._public(db.execute('select * from browser_actions where id=?',(action_id,)).fetchone())

    def approve(self,payload,policy):
        _shape(payload,'id intent_digest confirmed')
        if payload['confirmed'] is not True:raise ValueError('请核对并确认这条具体排程')
        with self._tx() as db:
            self._sweep(db);r=db.execute('select * from browser_actions where id=?',(payload['id'],)).fetchone()
            if not r or r['state']!='draft' or not secrets.compare_digest(r['digest'],str(payload['intent_digest'])):raise ValueError('排程已失效或摘要不匹配')
            v=json.loads(r['intent']);self._account(v,policy)
            w=self._worker(db,r['worker_id'],r['epoch'],policy)
            sources=[normalize(s,policy) for s in json.loads(w['surfaces']) if s['account_id']==v['account_id'] and s['plan_id']==v['plan_id'] and 0<=(self._now()-_time(s['captured_at'])).total_seconds()<=FRESH_SECONDS]
            if r['revision']!=policy['revision'] or len(sources)!=1 or Decimal(v['budget'])>Decimal(sources[0]['parent_budget']):raise ValueError('账户、当前计划或预算已变化，请重新准备')
            if v.get('aweme_id') and (sources[0]['aweme_id']!=v['aweme_id'] or sources[0]['detail_url']!=v['detail_url'] or not 0<=(self._now()-_time(sources[0]['parent_captured_at'])).total_seconds()<=600):raise ValueError('计划与抖音号对应关系已变化或过期，请重新准备')
            if self._now()>=_time(v['trigger_at']):raise ValueError('开始时间已到，请重新设置具体排程')
            expires=(_time(v['trigger_at'])+timedelta(seconds=LATE_SECONDS)).isoformat()
            db.execute("update browser_actions set state='approved',expires_at=? where id=?",(expires,r['id']));self._event(db,r['id'],'approved',{'intent_digest':r['digest']})
        return {'ok':True,'state':'approved','executed':False}

    def has_pending(self):
        with self._tx() as db:
            self._sweep(db)
            for r in db.execute("select state,intent from browser_actions where state in ('approved','claimed','submitting','unknown')"):
                if r['state']!='approved' or _time(json.loads(r['intent'])['trigger_at'])<=self._now():return True
        return False

    def claim(self,payload,policy):
        _shape(payload,'worker_id epoch')
        with self._tx() as db:
            self._sweep(db);w=self._worker(db,payload['worker_id'],payload['epoch'],policy)
            if db.execute("select 1 from browser_actions where state in ('claimed','submitting','unknown')").fetchone():return {'ok':True,'command':None,'blocked':'已有调速执行中或结果不明'}
            for r in db.execute("select * from browser_actions where worker_id=? and state='approved' order by expires_at,id",(w['id'],)).fetchall():
                v=json.loads(r['intent'])
                if self._now()<_time(v['trigger_at']):continue
                try:self._account(v,policy)
                except ValueError:db.execute("update browser_actions set state='cancelled' where id=?",(r['id'],));continue
                if r['revision']!=policy['revision']:db.execute("update browser_actions set state='cancelled' where id=?",(r['id'],));continue
                current=[s for s in json.loads(w['surfaces']) if s['account_id']==v['account_id'] and s['plan_id']==v['plan_id']]
                if current and current[0]['busy'] and 0<=(self._now()-_time(current[0]['captured_at'])).total_seconds()<=10:
                    message='到点时原生起量／调速仍在进行；保留原任务，未提交控量。请处理冲突后重新安排。'
                    db.execute("update browser_actions set state='blocked',receipt=? where id=?",(_dump({'message':message}),r['id']))
                    self._event(db,r['id'],'blocked',{'message':message});continue
                lease=secrets.token_urlsafe(32)
                db.execute("update browser_actions set state='claimed',epoch=?,claimed_at=?,lease=? where id=?",(w['epoch'],self._now().isoformat(),lease,r['id']));self._event(db,r['id'],'claimed')
                return {'ok':True,'command':{'id':r['id'],'intent':v,'intent_digest':r['digest'],'expires_at':r['expires_at'],'lease':lease}}
        return {'ok':True,'command':None}

    def begin(self,payload,policy):
        _shape(payload,'id lease worker_id epoch observation')
        with self._tx() as db:
            self._sweep(db);r=db.execute('select * from browser_actions where id=?',(payload['id'],)).fetchone()
            if not r or r['state']!='claimed' or not secrets.compare_digest(r['lease'] or '',str(payload['lease'])):raise ValueError('排程提交许可无效')
            self._worker(db,payload['worker_id'],payload['epoch'],policy)
            if r['worker_id']!=payload['worker_id'] or r['epoch']!=payload['epoch'] or r['revision']!=policy['revision']:raise ValueError('排程执行会话变化')
            v=json.loads(r['intent']);self._account(v,policy);s=normalize(payload['observation'],policy)
            if v.get('aweme_id') and (s['aweme_id']!=v['aweme_id'] or s['detail_url']!=v['detail_url'] or not 0<=(self._now()-_time(s['parent_captured_at'])).total_seconds()<=90):raise ValueError('请重新核验当前计划与抖音号对应关系')
            if not _time(v['trigger_at'])<=self._now()<_time(r['expires_at']):raise ValueError('不在排程执行时间内')
            if any(s[k]!=v[k] for k in ('account_id','plan_id','group')) or not s['running'] or s['busy'] or Decimal(v['budget'])>Decimal(s['parent_budget']) or not 0<=(self._now()-_time(s['captured_at'])).total_seconds()<=10:raise ValueError('当前计划未投放、已有起量占用、预算变化或观察未就绪')
            db.execute("update browser_actions set state='submitting' where id=?",(r['id'],));self._event(db,r['id'],'submitting',{'surface':s})
        return {'ok':True,'submit_once':True}

    def receipt(self,payload):
        _shape(payload,'id lease submitted observation message')
        if type(payload['submitted']) is not bool or not isinstance(payload['message'],str) or len(payload['message'])>300:raise ValueError('排程回执无效')
        with self._tx() as db:
            self._sweep(db);r=db.execute('select * from browser_actions where id=?',(payload['id'],)).fetchone()
            if not r or not secrets.compare_digest(r['lease'] or '',str(payload['lease'])):raise ValueError('排程回执身份不匹配')
            clean={k:v for k,v in payload.items() if k!='lease'}
            if r['receipt']:
                if json.loads(r['receipt'])!=clean:raise ValueError('重复回执内容不一致')
                return {'ok':True,'state':r['state']}
            if r['state'] not in ('claimed','submitting','unknown') or payload['submitted'] and r['state']=='claimed':raise ValueError('排程回执状态不匹配')
            state='unknown' if payload['submitted'] or r['state'] in ('submitting','unknown') else 'rejected'
            if payload['submitted'] and self._verified(db,r,payload['observation']):state='verified'
            db.execute('update browser_actions set state=?,receipt=? where id=?',(state,_dump(clean),r['id']));self._event(db,r['id'],state,{'message':payload['message']})
        return {'ok':True,'state':state}

    def _verified(self,db,r,obs):
        if not isinstance(obs,dict):return False
        try:s=normalize(obs,{'accounts':[{'id':json.loads(r['intent'])['account_id'],'scene':'live','observe':True,'tab_group':json.loads(r['intent'])['group']}]})
        except (ValueError,TypeError):return False
        v=json.loads(r['intent']);event=db.execute("select at,detail from browser_action_journal where action_id=? and event='submitting' order by seq desc limit 1",(r['id'],)).fetchone()
        if not event:return False
        before=json.loads(event['detail'])['surface']
        if v.get('aweme_id') and (s['aweme_id']!=v['aweme_id'] or s['detail_url']!=v['detail_url']):return False
        return (all(s[k]==v[k] for k in ('account_id','plan_id','group')) and s['tab_id']==before['tab_id'] and s['document_id']!=before['document_id'] and
            s['control_mode']=='control' and s['busy'] and s['control_budget']==v['budget'] and s['control_minutes']==v['duration_minutes'] and
            s['control_created_at'] is not None and _time(s['control_created_at'])>=_time(event['at']) and 0<=(self._now()-_time(s['captured_at'])).total_seconds()<=10)

    def status(self,policy):
        with self._tx() as db:
            self._sweep(db);workers=[]
            for w in db.execute('select * from browser_workers order by seen_at desc'):
                current=w['revision']==policy['revision'] and 0<=(self._now()-_time(w['seen_at'])).total_seconds()<=FRESH_SECONDS
                sources=[]
                if current:
                    for s in json.loads(w['surfaces']):
                        try:
                            if 0<=(self._now()-_time(s['captured_at'])).total_seconds()<=FRESH_SECONDS:sources.append(normalize(s,policy))
                        except (ValueError,TypeError):continue
                checks=db.execute('select data_json from live_close_checks where worker_id=?',(w['id'],)).fetchone()
                checks=[c for c in json.loads(checks[0]) if c['account_id'] in {a['id'] for a in policy['accounts'] if a['scene']=='live' and a.get('observe',True)}] if checks and current else []
                workers.append({'worker_id':w['id'],'epoch':w['epoch'],'armed':bool(w['armed']) and current,'current':current,'surfaces':sources,'view_checks':checks})
            actions=[self._public(r) for r in db.execute('select * from browser_actions order by created_at desc limit 100')]
        return {'workers':workers,'actions':actions,'lead_minutes':20,'review_minutes':15,'late_minutes':2}
