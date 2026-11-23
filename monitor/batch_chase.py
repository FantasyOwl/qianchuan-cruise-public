"""Immutable, explicitly approved native material batches. No platform API IO."""
import copy
import json
import secrets
from datetime import timedelta
from decimal import Decimal
from .browser_actions import BrowserActions, FRESH_SECONDS, APPROVAL_SECONDS, _shape, _id, _number, _time, _dump, _hash
from .cruise_rules import material_chase_allowed

BATCH_REVIEW_SECONDS = 15 * 60


def date_range(now, days):
    if type(days) is not int or not 3 <= days <= 5:raise ValueError('素材统计天数须为3至5天')
    return ((now.date()-timedelta(days=days-1)).isoformat(),now.date().isoformat())


def normalize_surface(value, accounts):
    _shape(value,'account_id plan_id tab_id document_id path group captured_at date_start date_end page page_size sort materials editor_open')
    s=copy.deepcopy(value);_id(s['account_id']);_id(s['plan_id']);_id(s['document_id'],opaque=True)
    account=next((a for a in accounts if a['id']==s['account_id'] and a.get('scene')=='live' and a.get('observe',True)),None)
    if not account or s['group']!=account['tab_group'] or s['path']!='/uni-prom/detail':raise ValueError('素材页账户、母计划或分组不匹配')
    if type(s['tab_id']) is not int or s['tab_id']<=0 or type(s['editor_open']) is not bool:raise ValueError('素材页身份无效')
    from datetime import date
    start=date.fromisoformat(s['date_start']);end=date.fromisoformat(s['date_end'])
    if not 2 <= (end-start).days <= 4:raise ValueError('素材范围须为含首尾3至5天')
    if type(s['page']) is not int or s['page']<1 or s['page_size']!=100 or s['sort']!='spend_desc':raise ValueError('素材须按消耗降序、每页100条读取')
    if not isinstance(s['materials'],list) or len(s['materials'])>100:raise ValueError('素材行数无效')
    ids=set()
    for row in s['materials']:
        _shape(row,'id name spend roi chase_status approved selectable')
        _id(row['id'])
        if row['id'] in ids:raise ValueError('同页素材ID重复')
        ids.add(row['id'])
        if not isinstance(row['name'],str) or len(row['name'])>200:raise ValueError('素材名称无效')
        for key in ['spend','roi']:
            v=row[key]
            if v is not None:
                if isinstance(v,bool) or not isinstance(v,(str,int,float)) or not Decimal(str(v)).is_finite() or Decimal(str(v))<0:raise ValueError('素材指标无效')
                row[key]=format(Decimal(str(v)),'f')
        if type(row['approved']) is not bool or type(row['selectable']) is not bool or not isinstance(row['chase_status'],str):raise ValueError('素材资格未明确')
    _time(s['captured_at'])
    return s


def binding(s):
    return {k:s[k] for k in ['account_id','plan_id','tab_id','document_id','path','group','date_start','date_end','page','page_size','sort']}


def matches(row, rule, *, allow_running=False):
    if not row['approved'] or not row['selectable'] or row['spend'] is None or row['roi'] is None:return False
    if not material_chase_allowed(row['chase_status'],allow_running):return False
    lo,hi,roi=(rule.get(k) for k in ['material_spend_min','material_spend_max','material_roi_min'])
    return lo is not None and roi is not None and Decimal(row['spend'])>Decimal(str(lo)) and (hi is None or Decimal(row['spend'])<Decimal(str(hi))) and Decimal(row['roi'])>Decimal(str(roi))


def material_page_ready(surface):
    # Native status cells populate after the spend/ROI cells. A blank cell is
    # still loading; the platform renders an explicit '-' for no chase.
    return bool(surface['materials']) and all(row['chase_status'].strip() for row in surface['materials'])


def repeatable_material_ids(surface,profile):
    if profile['features']['f5'].get('allow_running_materials',False) is not True:return set()
    return {r['id'] for r in surface['materials'] if r['chase_status'].strip() in {'调控中','追投中'}}


def batch_candidates(surface, profile, now, reserved=()):
    from .cruise_rules import _in_window
    from .chase_batching import groups,batch_limits
    cfg=profile['features']['f5'];days=cfg.get('lookback_days',5);low,count=batch_limits(cfg);allow_running=cfg.get('allow_running_materials',False) is True
    start,end=date_range(now,days);notes=[];batches=[]
    if not cfg['enabled']:return {'batches':[],'notes':['本户F5未启用']}
    if not profile['enabled'] or not _in_window(now.strftime('%H:%M'),profile['operation_start'],profile['operation_end']):return {'batches':[],'notes':['本户未启用或不在允许操作时段']}
    b=profile['broadcast_protection']
    if b['enabled'] and _in_window(now.strftime('%H:%M'),b['start'],b['end']):return {'batches':[],'notes':['本户停播保护生效']}
    if (surface['date_start'],surface['date_end'])!=(start,end):return {'batches':[],'notes':[f'等待{start}至{end}素材观察']}
    if surface['editor_open']:return {'batches':[],'notes':['原页面存在编辑窗口，保留输入并暂停准备']}
    if not 0 <= (now-_time(surface['captured_at'])).total_seconds() <= FRESH_SECONDS:return {'batches':[],'notes':['素材观察已过期，等待重新读取']}
    if not material_page_ready(surface):return {'batches':[],'reading_complete':False,
        'notes':['素材追投状态尚未加载完整，保留本页继续读取，不作为未命中或继续翻页'],
        'scanned':len(surface['materials']),'page':surface['page'],'date_start':start,'date_end':end,'batch_size':count}
    used=set(reserved)
    for index,rule in enumerate(cfg['rules']):
        if rule.get('enabled',True) is False:continue
        if any(rule.get(k) is None for k in ('material_spend_min','material_roi_min')):notes.append(f'规则{index+1}素材消耗下限或ROI下限未设置');continue
        fields=('daily_budget','bid_value','optimization_target') if rule['chase_type']=='控成本追投' else ('total_budget','control_duration')
        if any(rule.get(k) is None for k in fields):notes.append(f'规则{index+1}创建参数不完整');continue
        selected=[r for r in surface['materials'] if r['id'] not in used and matches(r,rule,allow_running=allow_running)]
        selected.sort(key=lambda r:(-Decimal(r['spend']),r['id']))
        consumed=0
        for group in groups(selected,cfg):
            if len(batches)>=profile['safety']['max_actions_per_round']:break
            consumed+=len(group);ids=[r['id'] for r in group];used.update(ids)
            batches.append({'key':_hash([binding(surface),index,ids,rule,days,count,allow_running])[:24],
                'account_id':surface['account_id'],'plan_id':surface['plan_id'],'date_start':start,'date_end':end,
                'rule_index':index,'material_ids':ids,'materials':group,'after':{k:rule[k] for k in ('chase_type',)+fields},
                'surface':binding(surface),'budget_scope':'whole_batch','requires_confirmation':True,'allow_running_materials':allow_running})
        if len(selected)>consumed:notes.append(f'规则{index+1}本页还余{len(selected)-consumed}条合格素材，当前每批{low}–{count}条；ID保留在本页观察中')
    if not batches:notes.append('当前页没有凑齐符合本户规则的一批素材；'+('允许调控中素材再次追投，审核不通过或状态不明的素材仍不入选' if allow_running else '未允许调控中素材再次追投，审核不通过的素材也不入选'))
    return {'batches':batches,'notes':notes,'scanned':len(surface['materials']),'page':surface['page'],
            'date_start':start,'date_end':end,'batch_size':count,'batch_size_min':low,'batch_size_max':count}


class BatchChaseActions(BrowserActions):
    def __init__(self,path,profile_provider,clock=None):
        from .schema import now_cn
        super().__init__(path,clock=clock or now_cn);self.profile_provider=profile_provider
        with self._tx() as db:db.execute('create table if not exists batch_readback_requests(id text primary key,requested_at text,state text)')
        with self._tx() as db:db.execute('create table if not exists batch_view_checks(worker_id text primary key,at text,checks_json text)')

    def request_readback(self,payload,policy):
        _shape(payload,'id')
        with self._tx() as db:
            row=db.execute('select * from browser_actions where id=?',(payload['id'],)).fetchone()
            if not row or row['state']!='unknown':raise ValueError('仅对创建结果不明的批次重新读取')
            v=json.loads(row['intent'])
            if not any(a['id']==v['account_id'] and a.get('observe',True) for a in policy['accounts']):raise ValueError('该账户不在当前读取范围')
            db.execute('insert or replace into batch_readback_requests values(?,?,?)',(row['id'],self._now().isoformat(),'queued'))
            self._event(db,row['id'],'readback_requested')
        return {'ok':True,'ad_changes':0}

    def _read_worker(self,db,payload,policy):
        worker=db.execute('select * from browser_workers where id=?',(payload['worker_id'],)).fetchone()
        if not worker or worker['epoch']!=payload['epoch'] or worker['revision']!=policy['revision'] or not 0<=(self._now()-_time(worker['seen_at'])).total_seconds()<=FRESH_SECONDS:raise ValueError('读取执行器会话已变化或通信过期')

    def readback_jobs(self,payload,policy):
        _shape(payload,'worker_id epoch')
        with self._tx() as db:
            self._read_worker(db,payload,policy);commands=[]
            for r in db.execute("select a.* from browser_actions a join batch_readback_requests b on a.id=b.id where b.state='queued' and a.state='unknown'").fetchall():
                v=json.loads(r['intent'])
                if not any(a['id']==v['account_id'] and a.get('observe',True) and a['tab_group']==v['surface']['group'] for a in policy['accounts']):continue
                commands.append({'id':r['id'],'intent':v,'intent_digest':r['digest']})
        return {'ok':True,'commands':commands[:10]}

    def _valid_readback(self,db,row,observation):
        if not isinstance(observation,dict):return False
        _shape(observation,'account_id plan_id task_id task_name material_ids after captured_at tab_id document_id readback_document_id reloaded')
        _id(observation['task_id']);_id(observation['readback_document_id'],opaque=True)
        v=json.loads(row['intent']);submitted=db.execute("select at from browser_action_journal where action_id=? and event='submitting' order by seq desc limit 1",(row['id'],)).fetchone()
        return (all(observation[k]==v['surface'][k] for k in ['account_id','plan_id','tab_id','document_id']) and
            observation['readback_document_id']!=observation['document_id'] and observation['reloaded'] is True and
            observation['task_name']==v['task_name'] and isinstance(observation['material_ids'],list) and sorted(observation['material_ids'])==sorted(v['material_ids']) and
            isinstance(observation['after'],dict) and observation['after']==v['after'] and all(type(observation['after'].get(k)) is not bool for k in v['after']) and
            submitted is not None and _time(observation['captured_at'])>=_time(submitted['at']) and 0<=(self._now()-_time(observation['captured_at'])).total_seconds()<=10)

    def readback_result(self,payload,policy):
        _shape(payload,'id worker_id epoch observation')
        with self._tx() as db:
            self._read_worker(db,payload,policy)
            r=db.execute('select * from browser_actions where id=?',(payload['id'],)).fetchone()
            req=db.execute("select * from batch_readback_requests where id=? and state='queued'",(payload['id'],)).fetchone()
            if not r or not req or r['state']!='unknown':raise ValueError('没有本批次待处理的只读核验请求')
            v=json.loads(r['intent'])
            if not any(a['id']==v['account_id'] and a.get('observe',True) and a['tab_group']==v['surface']['group'] for a in policy['accounts']):raise ValueError('原账户读取范围已变化')
            verified=self._valid_readback(db,r,payload['observation'])
            if verified:
                db.execute("update browser_actions set state='verified' where id=?",(r['id'],));self._event(db,r['id'],'verified',{'independent_readback':payload['observation']})
            db.execute("update batch_readback_requests set state='done' where id=?",(r['id'],))
        return {'ok':True,'state':'verified' if verified else 'unknown','ad_changes':0}

    def observe(self,payload,policy):
        _shape({k:v for k,v in payload.items() if k!='view_checks'},'protocol worker_id epoch armed revision surfaces')
        if payload['protocol']!=1 or type(payload['armed']) is not bool or payload['revision']!=policy['revision']:raise ValueError('批量追投协议或范围已改变')
        _id(payload['worker_id'],opaque=True);_id(payload['epoch'],opaque=True)
        if not isinstance(payload['surfaces'],list) or len(payload['surfaces'])>100:raise ValueError('素材页清单无效')
        surfaces=[normalize_surface(s,policy['accounts']) for s in payload['surfaces']]
        if len({(s['account_id'],s['plan_id']) for s in surfaces})!=len(surfaces):raise ValueError('母计划素材页不唯一')
        if any(not 0<=(self._now()-_time(s['captured_at'])).total_seconds()<=FRESH_SECONDS for s in surfaces):raise ValueError('素材观察过期')
        checks=payload.get('view_checks',[])
        if not isinstance(checks,list) or len(checks)>100:raise ValueError('分页核验清单无效')
        import re
        for c in checks:
            _shape(c,'account_id plan_id tab_id code before after');_id(c['account_id']);_id(c['plan_id'])
            if not any(a['id']==c['account_id'] and a.get('observe',True) for a in policy['accounts']):raise ValueError('分页核验账户越界')
            if type(c['tab_id']) is not int or c['tab_id']<=0 or not isinstance(c['code'],str) or not re.fullmatch('[a-z_]{1,100}',c['code']):raise ValueError('分页核验状态无效')
            for side in ('before','after'):
                if c[side] is None:continue
                _shape(c[side],'page_size page descending rows')
                if type(c[side]['descending']) is not bool or any(c[side][k] is not None and (type(c[side][k]) is not int or not 0<=c[side][k]<=100000) for k in ('page_size','page','rows')):raise ValueError('分页核验数值无效')
        with self._tx() as db:
            self._sweep(db);old=db.execute('select * from browser_workers where id=?',(payload['worker_id'],)).fetchone()
            if old and (old['epoch']!=payload['epoch'] or not payload['armed']):
                for row in db.execute("select id,state from browser_actions where worker_id=? and state in ('draft','approved','claimed','submitting')",(payload['worker_id'],)).fetchall():
                    state='cancelled' if row['state'] in {'draft','approved'} else 'unknown'
                    db.execute('update browser_actions set state=? where id=?',(state,row['id']));self._event(db,row['id'],state)
            db.execute('insert or replace into browser_workers values(?,?,?,?,?,?)',(payload['worker_id'],payload['epoch'],int(payload['armed']),policy['revision'],self._now().isoformat(),_dump(surfaces)))
            db.execute('insert or replace into batch_view_checks values(?,?,?)',(payload['worker_id'],self._now().isoformat(),_dump(checks)))
        return {'ok':True}

    def _reserved(self,db,aid,exclude=None,repeatable=()):
        result=[]
        for r in db.execute('select * from browser_actions').fetchall():
            if r['id']==exclude:continue
            v=json.loads(r['intent'])
            if v['account_id']!=aid:continue
            if r['state'] in {'draft','approved','claimed','submitting','unknown'}:result+=v['material_ids']
        return result

    def _preview(self,s,db,exclude=None):
        profile,revision=self.profile_provider(s['account_id'])
        result=batch_candidates(s,profile,self.clock(),self._reserved(db,s['account_id'],exclude,repeatable_material_ids(s,profile)))
        used=0
        for r in db.execute('select id,state,created_at,intent from browser_actions').fetchall():
            if r['id']==exclude:continue
            v=json.loads(r['intent'])
            if v['account_id']==s['account_id'] and r['state'] in {'draft','approved','claimed','submitting','unknown','verified'} and _time(r['created_at']).astimezone(self.clock().tzinfo).date()==self.clock().date():used+=1
        available=max(0,profile['safety']['max_actions_per_day']-used)
        if len(result['batches'])>available:result['notes'].append('已达到本户单日批量动作上限');result['batches']=result['batches'][:available]
        for c in result['batches']:c['profile_revision']=revision
        return result

    def create(self,payload,policy):
        _shape(payload,'worker_id epoch account_id plan_id candidate_key')
        with self._tx() as db:
            self._sweep(db)
            s=normalize_surface(self._surface(db,payload['worker_id'],payload['epoch'],policy['revision'],payload['account_id'],payload['plan_id']),policy['accounts'])
            c=next((c for c in self._preview(s,db)['batches'] if c['key']==payload['candidate_key']),None)
            if c is None:raise ValueError('该批素材已变化、已占用或条件不再满足，请重新扫描')
            return self._draft(db,c,payload['worker_id'],payload['epoch'],policy)

    def create_selected(self,payload,policy):
        _shape(payload,'worker_id epoch account_id plan_id material_ids rule_index profile_revision')
        ids=payload['material_ids']
        if not isinstance(ids,list) or not 1<=len(ids)<=20 or any(not isinstance(mid,str) for mid in ids) or len(set(ids))!=len(ids):raise ValueError('需明确本批不重复的素材ID')
        for mid in ids:_id(mid)
        if type(payload['rule_index']) is not int or payload['rule_index']<0:raise ValueError('需指定本户规则')
        with self._tx() as db:
            self._sweep(db)
            s=normalize_surface(self._surface(db,payload['worker_id'],payload['epoch'],policy['revision'],payload['account_id'],payload['plan_id']),policy['accounts'])
            if not material_page_ready(s):raise ValueError('素材追投状态尚未加载完整，请等待本页读取')
            selected={**s,'materials':[r for r in s['materials'] if r['id'] in ids]}
            c=next((c for c in self._preview(selected,db)['batches'] if sorted(c['material_ids'])==sorted(ids) and
                c['rule_index']==payload['rule_index'] and c['profile_revision']==payload['profile_revision']),None)
            if c is None:raise ValueError('所选素材、当前规则或动作额度不再满足，请重新读取')
            return self._draft(db,c,payload['worker_id'],payload['epoch'],policy)

    def _draft(self,db,c,worker_id,epoch,policy):
        action_id=secrets.token_hex(16);now=self._now()
        intent={**c,'target_kind':'material_batch','action':'create_material_batch','action_label':'创建一批素材追投',
            'before':None,'field':'creation','reason':'近几天素材消耗及整体支付ROI命中本户规则；按整批任务确认',
            'task_name':'QC_'+self.clock().strftime('%Y%m%d')+'_'+action_id[:16]}
        expires=(now+timedelta(seconds=BATCH_REVIEW_SECONDS)).isoformat()
        db.execute('insert into browser_actions values(?,?,?,?,?,?,?,?,?,?,?,?)',(action_id,'draft',now.isoformat(),expires,worker_id,epoch,policy['revision'],_dump(intent),_hash(intent),None,None,None))
        self._event(db,action_id,'draft');return self._public(db.execute('select * from browser_actions where id=?',(action_id,)).fetchone())

    def renew(self,payload,policy):
        _shape(payload,'id')
        with self._tx() as db:
            self._sweep(db)
            old=db.execute('select * from browser_actions where id=?',(payload['id'],)).fetchone()
            if not old or old['state']!='expired' or old['claimed_at']:raise ValueError('仅能重新准备过期且未执行的确认单')
            v=json.loads(old['intent'])
            worker=db.execute('select * from browser_workers where id=?',(old['worker_id'],)).fetchone()
            if not worker:raise ValueError('等待原Chrome重新读取素材')
            s=normalize_surface(self._surface(db,worker['id'],worker['epoch'],policy['revision'],v['account_id'],v['plan_id']),policy['accounts'])
            if not material_page_ready(s):raise ValueError('素材追投状态尚未加载完整，请等待本页读取')
            # Renew the reviewed IDs, rather than regrouping them with newly
            # eligible rows. The ordinary candidate path still checks every
            # ID, current rule, reservation, time window and daily action cap.
            same_batch={**s,'materials':[r for r in s['materials'] if r['id'] in v['material_ids']]}
            c=next((c for c in self._preview(same_batch,db)['batches'] if sorted(c['material_ids'])==sorted(v['material_ids']) and
                c['after']==v['after'] and c['profile_revision']==v['profile_revision'] and c['rule_index']==v['rule_index'] and
                c['date_start']==v['date_start'] and c['date_end']==v['date_end'] and c['surface']['group']==v['surface']['group']),None)
            if c is None:raise ValueError('原批次的素材、规则或范围已变化；请扫描后准备新批次，不会替换素材或沿用旧确认')
            result=self._draft(db,c,worker['id'],worker['epoch'],policy)
            self._event(db,result['id'],'renewed_from',{'id':old['id']})
            return result

    def approve(self,payload,policy):
        _shape(payload,'id intent_digest confirmed')
        if payload['confirmed'] is not True:raise ValueError('需要对此批具体素材及整批金额确认')
        with self._tx() as db:
            self._sweep(db)
            row=db.execute('select * from browser_actions where id=?',(payload['id'],)).fetchone()
            if not row or row['state']!='draft' or not secrets.compare_digest(row['digest'],str(payload['intent_digest'])):
                raise ValueError('确认单已失效、已确认或内容摘要不匹配')
            self._check(db,row,policy)
            expires=(self._now()+timedelta(seconds=APPROVAL_SECONDS)).isoformat()
            db.execute("update browser_actions set state='approved',expires_at=? where id=?",(expires,row['id']))
            self._event(db,row['id'],'approved',{'intent_digest':row['digest'],'execution_expires_at':expires})
            return {'ok':True,'id':row['id'],'state':'approved','expires_at':expires,'executed':False}

    def _check(self,db,row,policy):
        if row['revision']!=policy['revision']:raise ValueError('采集范围已改变')
        v=json.loads(row['intent']);s=normalize_surface(self._surface(db,row['worker_id'],row['epoch'],policy['revision'],v['account_id'],v['plan_id']),policy['accounts'])
        p,revision=self.profile_provider(v['account_id'])
        if revision!=v['profile_revision'] or binding(s)!=v['surface'] or s['editor_open']:raise ValueError('规则、日期范围、页码或原页面已改变')
        if (s['date_start'],s['date_end'])!=date_range(self.clock(),p['features']['f5'].get('lookback_days',5)):raise ValueError('统计日期已跨天，请重新扫描')
        from .cruise_rules import _in_window
        if not p['enabled'] or not p['features']['f5']['enabled'] or not _in_window(self.clock().strftime('%H:%M'),p['operation_start'],p['operation_end']):raise ValueError('本户功能关闭或不在允许操作时段')
        b=p['broadcast_protection']
        if b['enabled'] and _in_window(self.clock().strftime('%H:%M'),b['start'],b['end']):raise ValueError('停播保护生效')
        cfg=p['features']['f5'];rule=cfg['rules'][v['rule_index']];allow_running=cfg.get('allow_running_materials',False) is True
        if v.get('allow_running_materials',False)!=allow_running:raise ValueError('调控中素材重复追投选项已变化，请重新准备')
        rows={r['id']:r for r in s['materials']}
        if any(mid not in rows or not matches(rows[mid],rule,allow_running=allow_running) for mid in v['material_ids']):raise ValueError('本批素材不再符合原规则')
        if set(v['material_ids'])&set(self._reserved(db,v['account_id'],row['id'],repeatable_material_ids(s,p))):raise ValueError('本批素材已有其他待处理或近期已创建追投')
        return v

    def begin(self,payload,policy):
        _shape(payload,'id lease observation')
        with self._tx() as db:
            self._sweep(db);r=db.execute('select * from browser_actions where id=?',(payload['id'],)).fetchone()
            if not r or r['state']!='claimed' or not secrets.compare_digest(r['lease'] or '',str(payload['lease'])):raise ValueError('提交许可无效或已消费')
            if _time(r['expires_at'])<=self._now():raise ValueError('执行许可已过期，需要重新核验和确认')
            intent=self._check(db,r,policy);s=normalize_surface(payload['observation'],policy['accounts'])
            if binding(s)!=intent['surface'] or not 0<=(self._now()-_time(s['captured_at'])).total_seconds()<=10:raise ValueError('提交前页面或统计范围已改变')
            p,_=self.profile_provider(intent['account_id']);cfg=p['features']['f5'];rule=cfg['rules'][intent['rule_index']];rows={r['id']:r for r in s['materials']}
            if any(mid not in rows or not matches(rows[mid],rule,allow_running=cfg.get('allow_running_materials',False) is True) for mid in intent['material_ids']):raise ValueError('提交前素材条件已改变')
            db.execute("update browser_actions set state='submitting' where id=?",(r['id'],));self._event(db,r['id'],'submitting')
            return {'ok':True,'submit_once':True}

    def receipt(self,payload):
        _shape(payload,'id lease submitted observation message')
        if type(payload['submitted']) is not bool or not isinstance(payload['message'],str) or len(payload['message'])>500:raise ValueError('批量回执无效')
        with self._tx() as db:
            self._sweep(db);r=db.execute('select * from browser_actions where id=?',(payload['id'],)).fetchone()
            if not r or not secrets.compare_digest(r['lease'] or '',str(payload['lease'])):raise ValueError('批量回执身份不符')
            clean={k:v for k,v in payload.items() if k!='lease'}
            if r['receipt']:
                if json.loads(r['receipt'])!=clean:raise ValueError('重复回执内容不同')
                return {'ok':True,'state':r['state']}
            if r['state'] not in {'claimed','submitting','unknown'} or payload['submitted'] and r['state']=='claimed':raise ValueError('回执状态不符')
            state='unknown' if payload['submitted'] or r['state'] in {'submitting','unknown'} else 'rejected'
            if payload['submitted'] and self._valid_readback(db,r,payload['observation']):state='verified'
            # A success toast is not task readback. Never retry an uncertain creation.
            db.execute('update browser_actions set state=?,receipt=? where id=?',(state,_dump(clean),r['id']));self._event(db,r['id'],state,{'message':payload['message']})
            return {'ok':True,'state':state,'verified':state=='verified'}

    def status(self,policy):
        with self._tx() as db:
            self._sweep(db);workers=[];previews=[]
            for r in db.execute('select * from browser_workers order by seen_at desc').fetchall():
                current=r['revision']==policy['revision'] and 0<=(self._now()-_time(r['seen_at'])).total_seconds()<=FRESH_SECONDS
                surfaces=[]
                if current:
                    for v in json.loads(r['surfaces']):
                        try:
                            s=normalize_surface(v,policy['accounts'])
                            if 0<=(self._now()-_time(s['captured_at'])).total_seconds()<=FRESH_SECONDS:surfaces.append(s)
                        except ValueError:continue
                for s in surfaces:previews.append({**self._preview(s,db),'worker_id':r['id'],'epoch':r['epoch'],'account_id':s['account_id'],'plan_id':s['plan_id'],'armed':bool(r['armed'])})
                workers.append({'worker_id':r['id'],'epoch':r['epoch'],'armed':bool(r['armed']) and current,'current':current,'surfaces':surfaces})
            actions=[self._public(r) for r in db.execute('select * from browser_actions order by created_at desc limit 100').fetchall()]
            checks=[c for r in db.execute('select at,checks_json from batch_view_checks') if 0<=(self._now()-_time(r['at'])).total_seconds()<=FRESH_SECONDS for c in json.loads(r['checks_json'])]
            return {'ok':True,'workers':workers,'previews':previews,'actions':actions,'view_checks':checks,'execution_available':any(w['armed'] and w['surfaces'] for w in workers),
                    'note':'近3至5天，按消耗排序，每页100条，同一批全部ID和整批预算逐批确认；提交结果不明不重试'}
