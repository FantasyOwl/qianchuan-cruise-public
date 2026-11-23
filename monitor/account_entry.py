"""Account-ID based, read-only browser entry jobs. Never approves an ad."""
import json
import re
import secrets
from urllib.parse import urlsplit, parse_qs, urlencode
from .schema import parse_time


class AccountEntryRuntime:
    def init_account_entry(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS cruise_account_entry_jobs(job_id TEXT PRIMARY KEY,purposes TEXT,result TEXT)')
        db.execute('CREATE TABLE IF NOT EXISTS cruise_account_routes(account_id TEXT PRIMARY KEY,group_name TEXT,url TEXT,plan_id TEXT,verified_at TEXT)')

    def _entry_url(self, account):
        today=self.clock().date().isoformat();query={'aavid':account['id']}
        if account['scene']=='live':query.update(ct='1',dr=today+','+today);path='/uni-prom'
        else:query.update(dateRange=today+','+today);path='/overall-prom'
        return 'https://qianchuan.jinritemai.com'+path+'?'+urlencode(query)

    def _queue_entry(self, db, account, lane, purpose):
        old=db.execute("SELECT j.id,e.purposes FROM cruise_jobs j JOIN cruise_account_entry_jobs e ON e.job_id=j.id WHERE j.account_id=? AND j.state IN ('queued','navigated') ORDER BY j.created_at LIMIT 1",(account['id'],)).fetchone()
        if old:
            purposes=set(json.loads(old['purposes']));purposes.add(purpose)
            db.execute('UPDATE cruise_account_entry_jobs SET purposes=? WHERE job_id=?',(json.dumps(sorted(purposes)),old['id']))
            return old['id']
        job_id=secrets.token_hex(12)
        db.execute('INSERT INTO cruise_jobs VALUES(?,?,?,?,?,?,?)',(job_id,account['id'],lane,self._entry_url(account),self.clock().isoformat(),'queued','按账户ID自动进入并识别计划；未修改投放'))
        db.execute('INSERT INTO cruise_account_entry_jobs VALUES(?,?,NULL)',(job_id,json.dumps([purpose])))
        return job_id

    def request_account_entry(self, account_id, purpose='data'):
        account=self._account(account_id)
        if account.get('observe',True) is not True:raise ValueError('请先启用本户Chrome采集')
        if purpose not in {'main','auto','data'}:raise ValueError('账户读取用途无效')
        with self._tx() as db:
            job_id=self._queue_entry(db,account,'auto' if purpose=='auto' else 'main',purpose)
            self._log(db,account_id,'account_entry_requested',{'job_id':job_id,'purpose':purpose,'ad_changes':0})
        return {'ok':True,'id':job_id,'job_ids':[job_id],'ad_changes':0,'note':'已按账户ID请求自动进入原Chrome；识别计划后继续读取，不批准投放。'}

    def account_entry_result(self, payload):
        if not isinstance(payload,dict) or set(payload)!={'id','ok','message','entry'} or type(payload['ok']) is not bool or not isinstance(payload['message'],str):raise ValueError('账户进入回执无效')
        e=payload['entry'];required={'code','account_id','group','url','plan_id','captured_at','tab_id','document_id','diagnostics'}
        if not isinstance(e,dict) or set(e)!=required or not isinstance(e['code'],str) or not re.fullmatch('[a-z_]{1,60}',e['code']):raise ValueError('账户进入证据无效')
        if not isinstance(e['diagnostics'],dict) or len(json.dumps(e['diagnostics'],ensure_ascii=False))>2000:raise ValueError('账户进入诊断过长')
        if type(e['tab_id']) is not int or e['tab_id']<0 or not isinstance(e['document_id'],str) or len(e['document_id'])>200:raise ValueError('账户进入文档无效')
        if not 0<=(self.clock()-parse_time(e['captured_at'])).total_seconds()<=90:raise ValueError('账户进入回执已过期')
        ready=e['code']=='ready'
        if payload['ok'] is not ready:raise ValueError('账户进入状态不一致')
        with self._tx() as db:
            row=db.execute('SELECT j.*,e.purposes FROM cruise_jobs j JOIN cruise_account_entry_jobs e ON e.job_id=j.id WHERE j.id=?',(payload['id'],)).fetchone()
            if not row or row['state'] not in {'queued','navigated'}:raise ValueError('账户进入任务已失效')
            account=self._account(row['account_id'])
            if not account.get('observe',True) or e['account_id']!=account['id'] or e['group']!=account['tab_group']:raise ValueError('账户进入范围已变化')
            if (self.clock()-parse_time(row['created_at'])).total_seconds()>600:raise ValueError('账户进入请求已过期')
            if ready:
                if e['tab_id']<=0 or not e['document_id'] or not isinstance(e['url'],str) or len(e['url'])>16000:raise ValueError('缺少账户页面证据')
                u=urlsplit(e['url']);q=parse_qs(u.query)
                if u.scheme!='https' or u.netloc!='qianchuan.jinritemai.com' or u.username or u.password or q.get('aavid')!=[account['id']]:raise ValueError('账户页面地址不匹配')
                if account['scene']=='live':
                    if u.path!='/uni-prom/detail' or not isinstance(e['plan_id'],str) or not re.fullmatch(r'\d{6,30}',e['plan_id']) or q.get('adId')!=[e['plan_id']]:raise ValueError('缺少真实直播计划ID')
                elif u.path!='/overall-prom' or e['plan_id'] is not None:raise ValueError('图文账户入口无效')
                db.execute('INSERT OR REPLACE INTO cruise_account_routes VALUES(?,?,?,?,?)',(account['id'],account['tab_group'],e['url'],e['plan_id'],e['captured_at']))
            state='completed' if ready else 'queued' if e['code'] in {'loading','view_switch_requested','detail_open_requested'} else 'failed'
            db.execute('UPDATE cruise_jobs SET state=?,message=? WHERE id=?',(state,payload['message'][:300],row['id']))
            db.execute('UPDATE cruise_account_entry_jobs SET result=? WHERE job_id=?',(json.dumps(e,ensure_ascii=False),row['id']))
            self._log(db,account['id'],'account_entry_result',{'code':e['code'],'plan_id':e['plan_id'],'message':payload['message'][:300],'ad_changes':0})
            purposes=set(json.loads(row['purposes']));saved=self._ensure(db,account)
            for lane in ('main','auto'):
                if saved[lane+'_running']:purposes.add(lane)
        if ready:
            if account['scene']=='product' or 'data' in purposes:self.request_data(account['id'])
            if account['scene']=='live':
                for lane in sorted(purposes & {'main','auto'}):self.scan(account['id'],lane)
        return {'ok':True,'state':state,'ad_changes':0}

    def _entry_status(self, db, account_id):
        row=db.execute('SELECT j.state,j.message,j.created_at,e.result FROM cruise_jobs j JOIN cruise_account_entry_jobs e ON e.job_id=j.id WHERE j.account_id=? ORDER BY j.created_at DESC LIMIT 1',(account_id,)).fetchone()
        if not row:return None
        result=json.loads(row['result']) if row['result'] else {}
        return {'state':row['state'],'message':row['message'],'code':result.get('code'),'plan_id':result.get('plan_id'),'at':result.get('captured_at',row['created_at'])}
