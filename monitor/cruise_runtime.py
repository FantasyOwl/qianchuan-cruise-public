"""Account-isolated replica of the original cruise lifecycle.

Starting a lane schedules observations and generates reviewable proposals. It
does not approve an advertisement change. Execution uses the separate queue.
"""
import copy
import hashlib
import json
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import timedelta
from urllib.parse import parse_qs, parse_qsl, urlencode, urlsplit, urlunsplit

from .schema import now_cn, parse_time
from .account_entry import AccountEntryRuntime


def _dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _revision(profile, bindings):
    return hashlib.sha256(_dump([profile, bindings]).encode()).hexdigest()


def view_url(value, account_id, lane, today):
    if not value:
        return None
    if not isinstance(value, str) or len(value) > 16000:
        raise ValueError('计划页面地址无效')
    u = urlsplit(value); query = parse_qs(u.query)
    if (u.scheme != 'https' or u.netloc != 'qianchuan.jinritemai.com' or u.username or u.password
            or query.get('aavid') != [account_id] or u.path != '/uni-prom/detail'
            or len(query.get('adId', [])) != 1 or not query['adId'][0].isdigit()):
        raise ValueError('调控页必须是该账户的明确直播计划详情地址')
    params = dict(parse_qsl(u.query, keep_blank_values=True)); params['dr'] = today + ',' + today
    fragment = dict(parse_qsl(u.fragment, keep_blank_values=True))
    try:
        detail = json.loads(fragment.get('uniDetail', '{}'))
    except (ValueError, TypeError):
        raise ValueError('计划页面参数无法读取') from None
    if not isinstance(detail, dict):
        raise ValueError('计划页面参数无法读取')
    detail['tb'] = 'uni_task_center' if lane == 'main' else 'creative'
    fragment['uniDetail'] = _dump(detail)
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(params), urlencode(fragment)))


class CruiseRuntime(AccountEntryRuntime):
    def __init__(self, path, account_provider, snapshot_provider, clock=None, history_provider=None):
        from .cruise_rules import default_profile, validate_profile, evaluate_account
        self.path = str(path); self.accounts = account_provider; self.snapshots = snapshot_provider
        self.clock = clock or now_cn; self.default_profile = default_profile
        self.history = history_provider or (lambda aid: [])
        self.validate_profile = validate_profile; self.evaluate = evaluate_account
        self.lock = threading.RLock()
        with self._tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS cruise_accounts(account_id TEXT PRIMARY KEY,profile TEXT,bindings TEXT,main_running INTEGER,auto_running INTEGER,main_due TEXT,auto_due TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS cruise_rounds(id TEXT PRIMARY KEY,account_id TEXT,lane TEXT,at TEXT,result TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS cruise_jobs(id TEXT PRIMARY KEY,account_id TEXT,lane TEXT,url TEXT,created_at TEXT,state TEXT,message TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS cruise_task_observations(object_key TEXT PRIMARY KEY,account_id TEXT,captured_at TEXT,data_json TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS cruise_logs(id INTEGER PRIMARY KEY,account_id TEXT,at TEXT,event TEXT,detail TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS cruise_action_links(action_id TEXT PRIMARY KEY,account_id TEXT,lane TEXT,profile_revision TEXT,candidate_key TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS cruise_material_view_hold(account_id TEXT PRIMARY KEY,until_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS cruise_home_view_hold(account_id TEXT PRIMARY KEY,until_at TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS cruise_home_sequences(account_id TEXT PRIMARY KEY,parent_job_id TEXT,home_job_id TEXT)')
            self.init_account_entry(db)

    @contextmanager
    def _tx(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10); db.row_factory = sqlite3.Row
            try:
                db.execute('BEGIN IMMEDIATE'); yield db; db.commit()
            except BaseException:
                db.rollback(); raise
            finally:
                db.close()

    def _account(self, account_id):
        account = next((a for a in self.accounts() if a['id'] == account_id), None)
        if account is None:
            raise ValueError('该账户不在当前多户配置中')
        return account

    def _ensure(self, db, account):
        row = db.execute('SELECT * FROM cruise_accounts WHERE account_id=?', (account['id'],)).fetchone()
        if row is None:
            profile = self.default_profile(account.get('scene', 'live'))
            db.execute('INSERT INTO cruise_accounts VALUES(?,?,?,0,0,NULL,NULL)',
                (account['id'], _dump(profile), _dump({'control_page_url': '', 'material_page_url': ''})))
            row = db.execute('SELECT * FROM cruise_accounts WHERE account_id=?', (account['id'],)).fetchone()
        return row

    def _log(self, db, aid, event, detail):
        db.execute('INSERT INTO cruise_logs(account_id,at,event,detail) VALUES(?,?,?,?)',
            (aid, self.clock().isoformat(), event, _dump(detail)))

    def save(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'account_id', 'expected_revision', 'profile', 'bindings'}:
            raise ValueError('保存需包含账户、配置版本、功能参数和页面绑定')
        account = self._account(payload['account_id'])
        profile = self.validate_profile(payload['profile'], account.get('scene', 'live'))
        bindings = payload['bindings']
        if not isinstance(bindings, dict) or set(bindings) != {'control_page_url', 'material_page_url'}:
            raise ValueError('需要调控页和素材页两个绑定字段')
        for key, lane in (('control_page_url', 'main'), ('material_page_url', 'auto')):
            if bindings[key]: view_url(bindings[key], account['id'], lane, self.clock().date().isoformat())
        with self._tx() as db:
            row = self._ensure(db, account)
            if payload['expected_revision'] != _revision(json.loads(row['profile']), json.loads(row['bindings'])):
                raise ValueError('此户配置已改变，请重新载入后保存')
            db.execute('UPDATE cruise_accounts SET profile=?,bindings=?,main_due=NULL,auto_due=NULL WHERE account_id=?',
                (_dump(profile), _dump(bindings), account['id']))
            self._log(db, account['id'], 'profile_saved', {'ad_changes': 0})
        return {'ok': True, 'account_id': account['id'], 'revision': _revision(profile, bindings), 'ad_changes': 0}

    def save_batch_range(self, payload):
        """Change only the F5 batch range; preserve every other saved rule."""
        fields={'account_id','expected_revision','batch_size_min','batch_size_max','confirmed'}
        if not isinstance(payload,dict) or set(payload)!=fields or payload['confirmed'] is not True:
            raise ValueError('请确认本户每批素材数量区间')
        account=self._account(payload['account_id'])
        low,high=payload['batch_size_min'],payload['batch_size_max']
        if type(low) is not int or type(high) is not int or not 2<=low<=high<=20:
            raise ValueError('每批素材数须为2至20的整数，最少不能大于最多')
        with self._tx() as db:
            row=self._ensure(db,account);profile=json.loads(row['profile']);bindings=json.loads(row['bindings'])
            if payload['expected_revision']!=_revision(profile,bindings):raise ValueError('此户配置已改变，请重新载入后保存')
            profile['features']['f5'].update(batch_size_min=low,batch_size_max=high,batch_size=high)
            db.execute('UPDATE cruise_accounts SET profile=?,main_due=NULL,auto_due=NULL WHERE account_id=?',(_dump(profile),account['id']))
            self._log(db,account['id'],'batch_range_saved',{'batch_size_min':low,'batch_size_max':high,'ad_changes':0})
        return {'ok':True,'account_id':account['id'],'revision':_revision(profile,bindings),'ad_changes':0}

    def control(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'account_id', 'lane', 'running'}:
            raise ValueError('需明确账户、主巡航/自动追投及启停状态')
        account = self._account(payload['account_id']); lane = payload['lane']
        if lane not in {'main', 'auto'} or type(payload['running']) is not bool:
            raise ValueError('巡航启停参数无效')
        if payload['running'] and account.get('observe', True) is not True:
            raise ValueError('请先开启该账户的采集')
        with self._tx() as db:
            row = self._ensure(db, account); profile = json.loads(row['profile'])
            if payload['running']: profile['enabled'] = True
            db.execute('UPDATE cruise_accounts SET '+lane+'_running=?, '+lane+'_due=NULL, profile=? WHERE account_id=?',
                (int(payload['running']), _dump(profile), account['id']))
            if not payload['running']:
                db.execute("UPDATE cruise_jobs SET state='cancelled' WHERE account_id=? AND lane=? AND state IN ('queued','navigated')", (account['id'], lane))
            self._log(db, account['id'], 'lane_started' if payload['running'] else 'lane_stopped',
                {'lane': lane, 'execution_mode': 'per_action_confirmation', 'ad_changes': 0})
        if payload['running']: self.scan(account['id'], lane)
        return {'ok': True, 'account_id': account['id'], 'lane': lane, 'running': payload['running'], 'ad_changes': 0}

    def _observations(self, aid):
        values = list(self.snapshots(aid))
        with self._tx() as db:
            tasks = [json.loads(r['data_json']) for r in db.execute('SELECT data_json FROM cruise_task_observations WHERE account_id=?', (aid,))]
        values.extend(tasks)
        return values

    def _source_url(self, aid, lane, bindings, snapshots):
        explicit = bindings['control_page_url' if lane == 'main' else 'material_page_url']
        if explicit: return view_url(explicit, aid, lane, self.clock().date().isoformat())
        # Both views belong to the same verified parent detail. A separate
        # material binding is optional; never invent a parent from old creatives.
        if lane == 'auto' and bindings.get('control_page_url'):
            return view_url(bindings['control_page_url'], aid, lane, self.clock().date().isoformat())
        with self._tx() as db:route=db.execute('SELECT * FROM cruise_account_routes WHERE account_id=?',(aid,)).fetchone()
        if route and route['group_name']==self._account(aid)['tab_group']:
            try:return view_url(route['url'],aid,lane,self.clock().date().isoformat())
            except ValueError:pass
        for s in sorted(snapshots, key=lambda x:x.get('source', {}).get('captured_at', ''), reverse=True):
            value = s.get('source', {}).get('url', '')
            if not value: continue
            try: return view_url(value, aid, lane, self.clock().date().isoformat())
            except ValueError: continue
        return None

    def scan(self, account_id, lane='main', *, request_collection=True, force_read=False):
        account = self._account(account_id)
        if account.get('observe', True) is not True: raise ValueError('该账户采集已关闭，不能巡检')
        if lane not in {'main', 'auto'}: raise ValueError('巡航类型无效')
        with self._tx() as db:
            row = self._ensure(db, account); profile = json.loads(row['profile']); bindings = json.loads(row['bindings'])
            revision = _revision(profile, bindings)
        snapshots = self._observations(account_id)
        effective = copy.deepcopy(profile); effective['enabled'] = True
        features = effective.get('features', {})
        for feature_id, value in features.items():
            if (lane == 'main' and feature_id == 'f5') or (lane == 'auto' and feature_id != 'f5'):
                value['enabled'] = False
        api_scanner=getattr(self,'official_scanner',None)
        api_result=api_scanner(account,profile,lane) if api_scanner else None
        result = api_result if api_result is not None else self.evaluate(account_id, effective, snapshots, now=self.clock(), history=self.history(account_id))
        if api_result is not None:request_collection=False
        result['profile_revision'] = revision
        proposals = result.get('proposals', [])
        for p in proposals: p.setdefault('execution_mode', 'per_action_confirmation')
        interval = profile.get('main_interval_seconds' if lane == 'main' else 'auto_interval_seconds', 300 if lane == 'main' else 600)
        now = self.clock()
        guarded = result['summary'] in {'本户巡航配置未启用', '不在本户操作时段', '本户停播保护生效，本轮不生成动作'}
        url = self._source_url(account_id, lane, bindings, snapshots) if request_collection and not guarded else None
        if lane=='auto' and url:
            from .batch_chase import date_range
            start,end=date_range(now,profile['features']['f5'].get('lookback_days',5))
            u=urlsplit(url);params=dict(parse_qsl(u.query,keep_blank_values=True));params['dr']=start+','+end
            fragment=dict(parse_qsl(u.fragment));detail=json.loads(fragment['uniDetail'])
            cc=json.loads(detail.get('cc','{}'));cc.update(p='1',ps='100',sf='stat_cost',st='desc')
            detail['cc']=_dump(cc);fragment['uniDetail']=_dump(detail)
            url=urlunsplit((u.scheme,u.netloc,u.path,urlencode(params),urlencode(fragment)))
        entry_needed=request_collection and not guarded and not url
        if entry_needed:
            result['diagnostics'].append({'feature_id':'f5' if lane=='auto' else 'account','status':'account_entry_pending',
                'reason':'正在按账户ID自动进入并识别真实计划，完成后继续本路读取'})
        with self._tx() as db:
            current = self._ensure(db, account)
            if _revision(json.loads(current['profile']), json.loads(current['bindings'])) != revision:
                raise ValueError('巡检期间该户参数已变化，本轮结果已丢弃')
            round_id = secrets.token_hex(12)
            db.execute('INSERT INTO cruise_rounds VALUES(?,?,?,?,?)', (round_id, account_id, lane, now.isoformat(), _dump(result)))
            db.execute('UPDATE cruise_accounts SET '+lane+'_due=? WHERE account_id=?',
                ((now + timedelta(seconds=interval)).isoformat(), account_id))
            if request_collection and url:
                old = db.execute("SELECT id,url,state FROM cruise_jobs WHERE account_id=? AND lane=? AND state IN ('queued','navigated')", (account_id, lane)).fetchone()
                if old and lane=='auto' and old['url']!=url:
                    held=db.execute('select 1 from cruise_material_view_hold where account_id=? and until_at>?',(account_id,now.isoformat())).fetchone()
                    same_range=parse_qs(urlsplit(old['url']).query).get('dr')==parse_qs(urlsplit(url).query).get('dr')
                    if held and same_range and not force_read:url=old['url']
                    else:db.execute("UPDATE cruise_jobs SET state='cancelled',message='素材统计范围已更新' WHERE id=?",(old['id'],));old=None
                if old and lane=='auto' and force_read and old['state']=='navigated':
                    db.execute("UPDATE cruise_jobs SET state='queued',created_at=?,message='手动请求重新读取近几天素材' WHERE id=?",(now.isoformat(),old['id']))
                if not old:
                    db.execute('INSERT INTO cruise_jobs VALUES(?,?,?,?,?,?,?)',
                        (secrets.token_hex(12), account_id, lane, url, now.isoformat(), 'queued', '等待读取原账户页面'))
            if entry_needed:self._queue_entry(db,account,lane,lane)
            self._log(db, account_id, 'scan_finished', {'lane': lane, 'rule_candidates': len(proposals), 'ad_changes': 0,
                **{k:result.get(k,0) for k in ('material_batches','materials_pending_confirmation','material_pages_read','material_rows_scanned','invalid_material_id_rows')},
                'collection_requested': bool(url) or entry_needed, 'missing_page_binding': entry_needed,
                'summary':result['summary'],'execution_mode':result.get('mode','per_action_confirmation'),'diagnostics':result.get('diagnostics',[])[:8],
                'missing_fields':list(dict.fromkeys(reason for item in result.get('skipped',[]) for reason in item.get('reasons',[]) if reason.startswith('缺少')))[:8]})
        return {'ok': True, 'id': round_id, 'account_id': account_id, 'lane': lane,
            'result': result, 'collection_requested': bool(url) or entry_needed, 'ad_changes': 0}

    def tick(self):
        now = self.clock(); due = []
        with self._tx() as db:
            for account in self.accounts():
                row = self._ensure(db, account)
                if account.get('observe', True) is not True: continue
                for lane in ('main', 'auto'):
                    if row[lane+'_running'] and (not row[lane+'_due'] or parse_time(row[lane+'_due']) <= now):
                        due.append((account['id'], lane))
        for aid, lane in due:
            try: self.scan(aid, lane)
            except Exception as exc:
                with self._tx() as db:
                    row=db.execute('SELECT profile FROM cruise_accounts WHERE account_id=?',(aid,)).fetchone()
                    profile=json.loads(row['profile'])
                    delay=max(60,profile.get(lane+'_interval_seconds',300 if lane=='main' else 600),getattr(exc,'retry_after',0))
                    next_at=(self.clock()+timedelta(seconds=delay)).isoformat()
                    db.execute('UPDATE cruise_accounts SET '+lane+'_due=? WHERE account_id=?',
                        (next_at, aid))
                    self._log(db, aid, 'scan_deferred' if getattr(exc,'reason',None) in {'rate_limited','service_busy'} else 'scan_error',
                        {'lane':lane,'error':type(exc).__name__,'message':str(exc)[:300],
                         'operation':getattr(exc,'operation',None),'reason':getattr(exc,'reason',None),
                         'retry_after_seconds':delay,'next_at':next_at,'ad_changes':0})

    def worker_jobs(self, revision, navigation_allowed):
        allowed = {a['id'] for a in self.accounts() if a.get('observe', True)}
        with self._tx() as db:
            now = self.clock()
            db.execute("UPDATE cruise_jobs SET state='expired',message='采集请求已超过10分钟' WHERE state IN ('queued','navigated') AND created_at<?", ((now-timedelta(minutes=10)).isoformat(),))
            jobs = []
            pending_material={r['account_id'] for r in db.execute("SELECT account_id FROM cruise_jobs WHERE lane='auto' AND state IN ('queued','navigated')")}
            held_material={r['account_id'] for r in db.execute('SELECT account_id FROM cruise_material_view_hold WHERE until_at>?',(now.isoformat(),))}
            held_home={r['account_id'] for r in db.execute('SELECT account_id FROM cruise_home_view_hold WHERE until_at>?',(now.isoformat(),))}
            home_step={}
            for sequence in db.execute('select * from cruise_home_sequences').fetchall():
                if sequence['account_id'] not in held_home:continue
                parent=db.execute('select state from cruise_jobs where id=?',(sequence['parent_job_id'],)).fetchone()
                if parent and parent['state'] in ('failed','expired','cancelled'):
                    db.execute("update cruise_jobs set state='failed',message='前置计划详情读取未完成，未切换首页' where id=? and state='queued'",(sequence['home_job_id'],));home_step[sequence['account_id']]=None
                else:home_step[sequence['account_id']]=sequence['parent_job_id'] if parent and parent['state']=='queued' else sequence['home_job_id']
            # Navigation is dispatched once. Repeating a navigated material job
            # after each observation reloads the page before status cells settle.
            for row in db.execute("SELECT * FROM cruise_jobs WHERE state='queued' ORDER BY created_at,id"):
                if row['account_id'] in allowed:
                    u=urlsplit(row['url']);query=parse_qs(u.query)
                    detail=json.loads(dict(parse_qsl(u.fragment)).get('uniDetail','{}'))
                    entry=db.execute('SELECT 1 FROM cruise_account_entry_jobs WHERE job_id=?',(row['id'],)).fetchone()
                    kind='account_entry' if entry else 'live_home' if u.path=='/uni-prom' else 'plan_data' if u.path=='/overall-prom' or detail.get('tb')=='data' else 'material_scan' if row['lane']=='auto' else 'control'
                    if kind!='account_entry' and row['account_id'] in held_home and (row['account_id'] in home_step and row['id']!=home_step[row['account_id']] or row['account_id'] not in home_step and kind!='live_home'):continue
                    if row['account_id'] in pending_material|held_material and row['lane']=='main' and kind=='control':continue
                    jobs.append({'id': row['id'], 'account_id': row['account_id'], 'lane': row['lane'],
                        'plan_id':query.get('adId',[''])[0], 'url':row['url'], 'job_kind':kind})
            jobs.sort(key=lambda j:0 if j['job_kind']=='account_entry' else 1 if j['job_kind']=='material_scan' else 2)
        return {'ok': True, 'protocol': 1, 'revision': revision, 'navigation_allowed': bool(navigation_allowed), 'jobs': jobs}

    def request_live_home(self,account_id):
        account=self._account(account_id)
        if account['scene']!='live' or account.get('observe',True) is not True:raise ValueError('请选择已启用采集的直播账户')
        now=self.clock();today=now.date().isoformat()
        with self._tx() as db:bindings=json.loads(self._ensure(db,account)['bindings'])
        source=self._source_url(account_id,'main',bindings,self._observations(account_id))
        parent_url=None
        if source:
            u=urlsplit(source);query=dict(parse_qsl(u.query,keep_blank_values=True));fragment=dict(parse_qsl(u.fragment,keep_blank_values=True))
            detail=json.loads(fragment.get('uniDetail','{}'));detail['tb']='data';fragment['uniDetail']=_dump(detail)
            parent_url=urlunsplit((u.scheme,u.netloc,u.path,urlencode(query),urlencode(fragment)))
            # Keep the verified plan's native delivery view and routing fields.
            # A bare /uni-prom defaults to another view and can show no rows.
            query['adId']='';query.pop('uniDetail',None);fragment.pop('uniDetail',None)
            url=urlunsplit((u.scheme,u.netloc,'/uni-prom',urlencode(query),urlencode(fragment)))
        else:
            url='https://qianchuan.jinritemai.com/uni-prom?'+urlencode({'aavid':account_id,'dr':today+','+today})
        with self._tx() as db:
            old=db.execute('select parent_job_id,home_job_id from cruise_home_sequences where account_id=?',(account_id,)).fetchone()
            if old:
                for previous in old:db.execute("update cruise_jobs set state='cancelled',message='由本户新的首页读取请求替代' where id=? and state='queued'",(previous,))
            job_id=secrets.token_hex(12);parent_id=secrets.token_hex(12) if parent_url else None
            if parent_url:db.execute('insert into cruise_jobs values(?,?,?,?,?,?,?)',(parent_id,account_id,'main',parent_url,now.isoformat(),'queued','先只读核验已绑定计划，再保留投放视图返回首页'))
            db.execute('insert into cruise_jobs values(?,?,?,?,?,?,?)',(job_id,account_id,'main',url,now.isoformat(),'queued','沿用已绑定计划视图只读刷新直播首页的一键调速入口'))
            db.execute('insert or replace into cruise_home_sequences values(?,?,?)',(account_id,parent_id,job_id))
            db.execute('insert or replace into cruise_home_view_hold values(?,?)',(account_id,(now+timedelta(minutes=5)).isoformat()))
            self._log(db,account_id,'live_home_requested',{'job_id':job_id,'ad_changes':0})
        return {'ok':True,'id':job_id,'parent_job_id':parent_id,'ad_changes':0,'note':'原Chrome先只读核验已绑定计划，再沿用投放视图返回首页，保留5分钟供设置排程；已有编辑窗口时不切页。'}

    def live_parent_mappings(self):
        """Recent explicit detail labels connect an aweme homepage row to a plan."""
        result=[]
        for account in self.accounts():
            if account['scene']!='live' or not account.get('observe',True):continue
            with self._tx() as db:bindings=json.loads(self._ensure(db,account)['bindings'])
            observations=self._observations(account['id']);detail=self._source_url(account['id'],'main',bindings,observations)
            if not detail:continue
            pid=parse_qs(urlsplit(detail).query).get('adId',[None])[0];owners=set();latest=None
            for s in observations:
                context=s.get('context',{});source=s.get('source',{});aweme=context.get('aweme_id')
                if s.get('scope')!='plan' or s.get('entity_id')!=pid or source.get('kind')!='browser_dom' or urlsplit(source.get('url','')).path!='/uni-prom/detail' or not isinstance(aweme,str) or not aweme.isascii() or not aweme.isdecimal():continue
                try:at=parse_time(source.get('captured_at',''))
                except (ValueError,TypeError):continue
                if not 0<=(self.clock()-at).total_seconds()<=600:continue
                owners.add(aweme)
                if latest is None or at>latest[0]:latest=(at,aweme)
            if len(owners)==1 and latest:
                result.append({'account_id':account['id'],'plan_id':pid,'aweme_id':latest[1],'group':account['tab_group'],'detail_url':detail,'captured_at':latest[0].isoformat()})
        return result

    def observe_material_pages(self, surfaces):
        with self._tx() as db:
            for row in db.execute("SELECT * FROM cruise_jobs WHERE lane='auto' AND state='navigated'").fetchall():
                u=urlsplit(row['url']);q=parse_qs(u.query);parent=q.get('adId',[None])[0];dates=q.get('dr',[''])[0]
                detail=json.loads(dict(parse_qsl(u.fragment)).get('uniDetail','{}'));cc=json.loads(detail.get('cc','{}'));page=int(cc.get('p',1))
                if any(s['account_id']==row['account_id'] and s['plan_id']==parent and not s['editor_open'] and
                       s['page']==page and s['page_size']==100 and s['sort']=='spend_desc' and
                       s['date_start']+','+s['date_end']==dates and parse_time(s['captured_at'])>parse_time(row['created_at']) for s in surfaces):
                    db.execute("UPDATE cruise_jobs SET state='completed',message='已收到指定日期范围的母计划素材页' WHERE id=?",(row['id'],))
                    db.execute('INSERT OR REPLACE INTO cruise_material_view_hold VALUES(?,?)',(row['account_id'],(self.clock()+timedelta(seconds=120)).isoformat()))

    def next_material_page(self, surface):
        """Read another native page only while the user's auto scanner runs."""
        aid=surface['account_id']
        with self._tx() as db:
            a=self._account(aid);row=self._ensure(db,a)
            if not row['auto_running'] or surface['editor_open'] or surface['page']>=100:return False
            profile=json.loads(row['profile']);cfg=profile['features']['f5']
            if not cfg['enabled'] or len(surface['materials'])<100:return False
            lows=[r.get('material_spend_min') for r in cfg['rules'] if r.get('enabled',True) and r.get('material_spend_min') is not None]
            last=surface['materials'][-1].get('spend')
            if not lows or last is None or float(last)<=min(lows):return False
            if db.execute("SELECT 1 FROM cruise_jobs WHERE account_id=? AND lane='auto' AND state IN ('queued','navigated')",(aid,)).fetchone():return False
            bindings=json.loads(row['bindings']);base=self._source_url(aid,'auto',bindings,[])
            if not base:return False
            u=urlsplit(base);q=dict(parse_qsl(u.query,keep_blank_values=True));q['dr']=surface['date_start']+','+surface['date_end']
            f=dict(parse_qsl(u.fragment));d=json.loads(f['uniDetail']);cc=json.loads(d.get('cc','{}'))
            cc.update(p=str(surface['page']+1),ps='100',sf='stat_cost',st='desc');d['cc']=_dump(cc);f['uniDetail']=_dump(d)
            url=urlunsplit((u.scheme,u.netloc,u.path,urlencode(q),urlencode(f)))
            db.execute('INSERT INTO cruise_jobs VALUES(?,?,?,?,?,?,?)',(secrets.token_hex(12),aid,'auto',url,self.clock().isoformat(),'queued','本页未凑齐批次，继续只读扫描下一页'))
        return True

    def request_data(self, account_id):
        account=self._account(account_id)
        if account.get('observe',True) is not True:raise ValueError('该账户采集已关闭')
        now=self.clock();today=now.date().isoformat()
        if account['scene']=='product':
            base='https://qianchuan.jinritemai.com/overall-prom?'+urlencode({'aavid':account_id,'dateRange':today+','+today})
            urls=[base+'#uniVideoTab='+view for view in ('1','2')]
        else:
            with self._tx() as db:bindings=json.loads(self._ensure(db,account)['bindings'])
            url=self._source_url(account_id,'main',bindings,self._observations(account_id))
            if not url:return self.request_account_entry(account_id,'data')
            u=urlsplit(url);fragment=dict(parse_qsl(u.fragment));detail=json.loads(fragment['uniDetail']);detail['tb']='data'
            fragment['uniDetail']=_dump(detail);url=urlunsplit((u.scheme,u.netloc,u.path,u.query,urlencode(fragment)))
            urls=[url]
        with self._tx() as db:
            ids=[]
            for url in urls:
                existing=db.execute("SELECT id FROM cruise_jobs WHERE account_id=? AND url=? AND state IN ('queued','navigated')",(account_id,url)).fetchone()
                job_id=existing['id'] if existing else secrets.token_hex(12);ids.append(job_id)
                if not existing:db.execute('INSERT INTO cruise_jobs VALUES(?,?,?,?,?,?,?)',(job_id,account_id,'main',url,now.isoformat(),'queued','等待只读刷新今天计划数据'))
            self._log(db,account_id,'data_refresh_requested',{'job_ids':ids,'ad_changes':0})
        return {'ok':True,'id':ids[0],'job_ids':ids,'ad_changes':0,'note':'已请求在本户现有Chrome标签只读刷新今天数据；商品的两个计划视图分别读取，不会批准调控。'}

    def locate_material_batch(self,intent):
        """Return to a previously reviewed native page without creating an ad."""
        aid=intent['account_id'];a=self._account(aid)
        if a['scene']!='live' or not a.get('observe',True) or a['tab_group']!=intent['surface']['group']:raise ValueError('原批次账户或分组已变化')
        with self._tx() as db:bindings=json.loads(self._ensure(db,a)['bindings'])
        base=self._source_url(aid,'auto',bindings,self._observations(aid))
        if not base:raise ValueError('等待本户已核验的计划绑定')
        u=urlsplit(base);q=dict(parse_qsl(u.query,keep_blank_values=True));f=dict(parse_qsl(u.fragment));d=json.loads(f['uniDetail'])
        if q.get('adId')!=intent['plan_id']:raise ValueError('当前绑定不是原批次母计划')
        q['dr']=intent['date_start']+','+intent['date_end'];d['tb']='creative';cc=json.loads(d.get('cc','{}'))
        cc.update(p=str(intent['surface']['page']),ps='100',sf='stat_cost',st='desc');d['cc']=_dump(cc);f['uniDetail']=_dump(d)
        url=urlunsplit((u.scheme,u.netloc,u.path,urlencode(q),urlencode(f)));job_id=secrets.token_hex(12)
        with self._tx() as db:
            db.execute("update cruise_jobs set state='cancelled',message='定位原确认批次素材页' where account_id=? and lane='auto' and state in ('queued','navigated')",(aid,))
            db.execute('insert into cruise_jobs values(?,?,?,?,?,?,?)',(job_id,aid,'auto',url,self.clock().isoformat(),'queued','只读返回已确认素材所在页，不更换素材或创建投放'))
            db.execute('insert or replace into cruise_material_view_hold values(?,?)',(aid,(self.clock()+timedelta(minutes=5)).isoformat()))
            self._log(db,aid,'batch_page_requested',{'job_id':job_id,'page':intent['surface']['page'],'plan_id':intent['plan_id'],'ad_changes':0})
        return {'ok':True,'id':job_id,'page':intent['surface']['page'],'ad_changes':0}

    def job_result(self, payload):
        if isinstance(payload,dict) and 'entry' in payload:return self.account_entry_result(payload)
        if not isinstance(payload, dict) or set(payload) != {'id', 'ok', 'message'} or type(payload['ok']) is not bool or not isinstance(payload['message'], str):
            raise ValueError('采集任务回执无效')
        with self._tx() as db:
            row = db.execute('SELECT * FROM cruise_jobs WHERE id=?', (payload['id'],)).fetchone()
            if not row or row['state'] not in {'queued', 'navigated'}: raise ValueError('采集任务已失效')
            self._account(row['account_id'])
            retry_identity=not payload['ok'] and payload['message']=='页面账户标识核验尚未通过，未刷新；请等待页面加载后重新扫描'
            db.execute('UPDATE cruise_jobs SET state=?,message=? WHERE id=?',
                ('navigated' if payload['ok'] else 'queued' if retry_identity else 'failed', payload['message'][:300], row['id']))
            self._log(db, row['account_id'], 'collection_navigation', {'lane': row['lane'], 'ok': payload['ok'], 'message': payload['message'][:300]})
        return {'ok': True, 'ad_changes': 0}

    def observe_tasks(self, surfaces):
        snapshots = []
        with self._tx() as db:
            for s in surfaces:
                aid = s['account_id']; self._account(aid)
                snapshot = {'id': 'cruise_'+hashlib.sha256(_dump(s).encode()).hexdigest()[:24], 'account_id': aid,
                    'entity_id': s['task_id'], 'scope': 'control', 'scene': 'live', 'promotion_type': 'control',
                    'metric_basis': s.get('metric_basis', 'control_payment'), 'metrics': {**s.get('metrics', {}), 'budget': float(s['budget']) if s.get('budget') else None},
                    'control': {'running': s.get('running')}, 'context': {**s.get('context', {}), 'parent_plan_id': s['plan_id']},
                    'source': {'kind': 'browser_dom', 'captured_at': parse_time(s['captured_at']).isoformat(), 'data_at': None,
                        'metric_date':s.get('context',{}).get('metric_date'),
                        'parent_entity_id': s['plan_id'], 'metric_period': 'unknown',
                        'url': 'https://qianchuan.jinritemai.com/uni-prom/detail?'+urlencode({'aavid':aid,'adId':s['plan_id']})},
                    'quality': {'issues': []}}
                key = _dump([aid, s['plan_id'], s['task_id']])
                db.execute('INSERT OR REPLACE INTO cruise_task_observations VALUES(?,?,?,?)', (key, aid, s['captured_at'], _dump(snapshot)))
                snapshots.append(snapshot)
        self.observations_arrived(snapshots)

    def observations_arrived(self, snapshots):
        satisfied = set()
        with self._tx() as db:
            for row in db.execute("SELECT * FROM cruise_jobs WHERE state IN ('queued','navigated') AND id NOT IN (SELECT job_id FROM cruise_account_entry_jobs)").fetchall():
                u=urlsplit(row['url']);query=parse_qs(u.query)
                # A control snapshot from the detail page cannot satisfy a
                # homepage read. Completion requires its own normalized row.
                if u.path=='/uni-prom':continue
                detail=json.loads(dict(parse_qsl(u.fragment)).get('uniDetail','{}'))
                expected_view=dict(parse_qsl(u.fragment)).get('uniVideoTab') if u.path=='/overall-prom' else None
                data_job=u.path=='/overall-prom' or detail.get('tb')=='data'
                scope = 'plan' if data_job else 'control' if row['lane'] == 'main' else 'creative'
                parent = query.get('adId',[None])[0]
                if any(s.get('account_id') == row['account_id'] and s.get('scope') == scope
                       and (parent is None or (s.get('entity_id') if data_job else s.get('source', {}).get('parent_entity_id') or s.get('context', {}).get('parent_plan_id')) == parent)
                       and (not data_job or s.get('source',{}).get('metric_date')==query.get('dr',query.get('dateRange',['']))[0].split(',')[0])
                       and (expected_view is None or s.get('context',{}).get('observed_plan_view')==expected_view)
                       and parse_time(s.get('source', {}).get('captured_at', '')) > parse_time(row['created_at']) for s in snapshots):
                    db.execute("UPDATE cruise_jobs SET state='completed',message='已收到本轮页面观察' WHERE id=?", (row['id'],))
                    if not data_job:satisfied.add((row['account_id'], row['lane']))
        for aid, lane in satisfied:
            self.scan(aid, lane, request_collection=False)

    def observe_live_home(self,surfaces):
        with self._tx() as db:
            for r in db.execute("select j.* from cruise_home_sequences h join cruise_jobs j on j.id=h.home_job_id where j.state='navigated'").fetchall():
                sequence=db.execute('select parent_job_id from cruise_home_sequences where home_job_id=?',(r['id'],)).fetchone()
                parent=db.execute('select url,state from cruise_jobs where id=?',(sequence[0],)).fetchone() if sequence[0] else None
                if parent and parent['state'] not in ('navigated','completed'):continue
                pid=parse_qs(urlsplit(parent[0]).query).get('adId',[None])[0] if parent else None
                if any(s['account_id']==r['account_id'] and s['path']=='/uni-prom' and (pid is None or s['plan_id']==pid) and parse_time(s['captured_at'])>parse_time(r['created_at']) for s in surfaces):
                    db.execute("update cruise_jobs set state='completed',message='已收到本户首页的明确计划行，未修改投放' where id=?",(r['id'],))

    @staticmethod
    def candidate_key(proposal):
        return _dump({k: proposal.get(k) for k in ('account_id','parent_plan_id','entity_id','object_type','feature_id','action','before','after')})

    def _current_candidates(self, account_id, lane, profile, exclude_action_id=None):
        effective = copy.deepcopy(profile); effective['enabled'] = True
        for fid, cfg in effective['features'].items():
            if (lane == 'main' and fid == 'f5') or (lane == 'auto' and fid != 'f5'): cfg['enabled'] = False
        history = [h for h in self.history(account_id) if h.get('id') != exclude_action_id]
        provider=getattr(self,'official_snapshot_provider',None)
        values=provider(account_id) if provider else None
        return self.evaluate(account_id, effective, self._observations(account_id) if values is None else values, now=self.clock(), history=history)['proposals']

    def proposal(self, account_id, proposal_id):
        account = self._account(account_id)
        with self._tx() as db:
            row = self._ensure(db, account); profile = json.loads(row['profile']); revision = _revision(profile, json.loads(row['bindings']))
            saved = None
            for r in db.execute('SELECT lane,result FROM cruise_rounds WHERE account_id=? ORDER BY at DESC LIMIT 100', (account_id,)):
                result = json.loads(r['result'])
                p = next((p for p in result.get('proposals', []) if p['id'] == proposal_id), None)
                if p: saved = (r['lane'], result.get('profile_revision'), p); break
        if not saved or saved[1] != revision: raise ValueError('巡航候选或本户配置已变化，请重新巡检')
        lane, _, old = saved; key = self.candidate_key(old)
        current = next((p for p in self._current_candidates(account_id, lane, profile) if self.candidate_key(p) == key), None)
        if current is None: raise ValueError('当前数据已不满足原巡航条件，请重新巡检')
        return {**current, 'review_lane': lane, 'profile_revision': revision, 'candidate_key': key}

    def bind_action(self, action_id, proposal):
        with self._tx() as db:
            db.execute('INSERT INTO cruise_action_links VALUES(?,?,?,?,?)', (action_id, proposal['account_id'],
                proposal['review_lane'], proposal['profile_revision'], proposal['candidate_key']))

    def rule_action_ids(self, account_id, lane):
        with self._tx() as db:
            return {r['action_id'] for r in db.execute('SELECT action_id FROM cruise_action_links WHERE account_id=? AND lane=?',(account_id,lane))}

    def validate_action(self, action_id, intent):
        with self._tx() as db:
            link = db.execute('SELECT * FROM cruise_action_links WHERE action_id=?', (action_id,)).fetchone()
            if link is None: return  # Explicit manual single-action intent, not rule certification.
            account = self._account(link['account_id']); row = self._ensure(db, account)
            profile = json.loads(row['profile']); revision = _revision(profile, json.loads(row['bindings']))
        if revision != link['profile_revision']: raise ValueError('本户巡航参数已改变，请重新准备调控单')
        current = self._current_candidates(link['account_id'], link['lane'], profile, exclude_action_id=action_id)
        matched = next((p for p in current if self.candidate_key(p) == link['candidate_key']), None)
        if matched is None: raise ValueError('原巡航条件已不满足，停止提交')
        if (intent['account_id'] != matched['account_id'] or intent['plan_id'] != matched['parent_plan_id']
                or intent['task_id'] != matched['entity_id'] or intent['action'] != matched['action']
                or intent['before'] != matched['before'] or intent['after'] != matched['after']):
            raise ValueError('调控单与当前巡航候选不一致')

    def status(self):
        accounts = []
        with self._tx() as db:
            for account in self.accounts():
                row = self._ensure(db, account); profile = json.loads(row['profile']); bindings = json.loads(row['bindings'])
                rounds = {}
                for lane in ('main', 'auto'):
                    last = db.execute('SELECT * FROM cruise_rounds WHERE account_id=? AND lane=? ORDER BY at DESC,id DESC LIMIT 1', (account['id'], lane)).fetchone()
                    rounds[lane] = {'running': bool(row[lane+'_running']), 'next_at': row[lane+'_due'],
                        'last_at': last['at'] if last else None, 'result': json.loads(last['result']) if last else None}
                accounts.append({**account, 'profile': profile, 'bindings': bindings, 'revision': _revision(profile, bindings), 'lanes': rounds,'entry':self._entry_status(db,account['id'])})
            logs = [{'id':r['id'],'account_id':r['account_id'],'at':r['at'],'event':r['event'],'detail':json.loads(r['detail'])}
                for r in db.execute('SELECT * FROM cruise_logs ORDER BY id DESC LIMIT 150')]
        return {'ok': True, 'accounts': accounts, 'logs': logs, 'execution_mode': 'per_action_confirmation'}
