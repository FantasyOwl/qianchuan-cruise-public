"""Explicitly confirmed parent-scoped native slowdown schedules.

The public smart-control API has no task ID or independent settings GET. Native
acceptance is therefore labelled as acknowledgement, never verified execution.
"""
import json
from datetime import timedelta
from .actuation import amount
from .schema import parse_time
from .official_api import OfficialBusinessError
from .official_workflow import active_tasks


class APISchedules:
    def __init__(self,actions):
        self.a=actions
        with actions.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS api_schedules(id TEXT PRIMARY KEY,account_id TEXT,plan_id TEXT,trigger_at TEXT,stop_at TEXT,state TEXT,result TEXT)')
            db.execute("UPDATE api_schedules SET state='unknown' WHERE state IN ('creating','stopping')")

    def status(self):
        with self.a.db() as db:return [dict(r)|{'result':json.loads(r['result']) if r['result'] else None} for r in db.execute('SELECT * FROM api_schedules')]

    def prepare(self,payload,account,plan):
        if account['scene']!='live':raise ValueError('下播控量仅适用于直播计划')
        off=parse_time(payload.get('off_at'));minutes=payload.get('duration_minutes')
        if type(minutes) is not int or minutes not in (30,60):raise ValueError('控量持续时间须为30或60分钟')
        start=off-timedelta(minutes=20);stop=start+timedelta(minutes=minutes)
        if start<=self.a.clock():raise ValueError('请设置尚未到达的下播前20分钟时点')
        budget=amount(payload.get('value'))
        if budget>amount(plan['budget']):raise ValueError('控量预算不能超过母计划预算')
        return {'before':None,'after':{'off_at':off.isoformat(),'trigger_at':start.isoformat(),'stop_at':stop.isoformat(),
                'budget':str(budget),'duration_minutes':minutes},'operation':'create_smart_control',
            'body':{'advertiser_id':int(account['id']),'ad_id':int(plan['plan_id']),'budget':float(budget)},
            'notes':['到点已有一键起量时保留原任务，本排程报告冲突并不创建控量。',
                '本排程独立于F1–F6规则更新；修改或取消下播控量须操作本排程，其他规则修改不会自动重建排程。',
                '官方控量API不返回任务ID，也没有独立参数查询；创建与停止均按母计划操作，只显示官方接受回执。',
                '本排程明确包含结束时停止此母计划当时的一键控量；期间请勿手工替换控量。保持本机服务运行才能按时启停。']}

    def register(self,rid,intent):
        value=intent['after'];message={'message':'具体API排程已确认，到点创建控量并在约定结束时间发送一次停止请求。','ad_changes':0}
        with self.a.db() as db:
            db.execute('INSERT INTO api_schedules VALUES(?,?,?,?,?,?,NULL)',(rid,intent['account_id'],intent['plan_id'],value['trigger_at'],value['stop_at'],'scheduled'))
            db.execute("UPDATE api_actions SET state='scheduled',updated_at=?,result=? WHERE id=?",(self.a.clock().isoformat(),json.dumps(message,ensure_ascii=False),rid))
            return self.a.record(db.execute('SELECT * FROM api_actions WHERE id=?',(rid,)).fetchone())

    def finish(self,rid,state,message,*,accepted=False):
        result={'message':message,'native_acknowledged':accepted,'independently_verified':False}
        with self.a.db() as db:db.execute('UPDATE api_schedules SET state=?,result=? WHERE id=?',(state,json.dumps(result,ensure_ascii=False),rid))
        self.a._finish(rid,state,result)

    def defer(self,row,exc,*,starting):
        """Retry only known-unsent work; never replay an uncertain/native POST."""
        now=self.a.clock();retry_at=now+timedelta(seconds=max(5,getattr(exc,'retry_after',0) or 30))
        if starting and retry_at>parse_time(row['trigger_at'])+timedelta(minutes=2):
            self.finish(row['id'],'blocked','接口冷却超出已确认的控量开始窗口，未发出创建请求：'+str(exc)[:250]);return
        state='scheduled' if starting else 'active_acknowledged'
        result={'message':'接口冷却，本次尚未发送'+('创建' if starting else '停止')+'请求；将在'+retry_at.isoformat()+'重新核验。',
            'retry_at':retry_at.isoformat(),'origin':getattr(exc,'origin',None),'reason':getattr(exc,'reason',None),
            'native_acknowledged':not starting,'independently_verified':False,'ad_changes':0}
        with self.a.db() as db:db.execute('UPDATE api_schedules SET state=?,result=? WHERE id=?',(state,json.dumps(result,ensure_ascii=False),row['id']))
        self.a._finish(row['id'],state,result)

    def tick(self):
        if not self.a.lock.acquire(blocking=False):return
        try:
            for row in self.status():
                now=self.a.clock();starting=row['state']=='scheduled' and parse_time(row['trigger_at'])<=now
                stopping=row['state']=='active_acknowledged' and parse_time(row['stop_at'])<=now
                if not (starting or stopping):continue
                if starting and now>parse_time(row['trigger_at'])+timedelta(minutes=2):
                    self.finish(row['id'],'missed','已错过下播前20分钟排程窗口，未补发控量。');continue
                retry=(row.get('result') or {}).get('retry_at')
                if retry and now<parse_time(retry):continue
                with self.a.db() as db:r=db.execute('SELECT * FROM api_actions WHERE id=?',(row['id'],)).fetchone()
                intent=json.loads(r['intent'])
                sent=False
                try:
                    account=self.a.account(row['account_id'])
                    with self.a.api.read_lock:
                        with self.a.api.lock:token=self.a.api._token(self.a.api.grant_for_account(account['id']))
                        if starting:
                            p=self.a._fresh(intent,account,token)['plan']
                            tasks=active_tasks(self.a.api,account,p,token,now,task_scene='SMART_BOOST')
                            if any(t['scene']=='SMART_BOOST' for t in tasks):
                                self.finish(row['id'],'blocked','到点仍有一键起量进行，保留原任务，未创建控量。');continue
                            if self.a.clock()>parse_time(row['trigger_at'])+timedelta(minutes=2):
                                self.finish(row['id'],'missed','核验完成时已错过控量开始窗口，未补发。');continue
                        with self.a.api.lock:token=self.a.api._token(self.a.api.grant_for_account(account['id']))
                        with self.a.gate,self.a.db() as db:
                            self.a.conflict_check()
                            prior='scheduled' if starting else 'active_acknowledged'
                            cursor=db.execute('UPDATE api_schedules SET state=? WHERE id=? AND state=?',('creating' if starting else 'stopping',row['id'],prior))
                            if cursor.rowcount!=1:continue
                        try:
                            sent=True
                            self.a.writer('create_smart_control' if starting else 'stop_smart_control',intent['body'] if starting else {
                                'advertiser_id':int(account['id']),'ad_id':int(row['plan_id']),'opt_type':'DISABLE'},token)
                        except OfficialBusinessError as exc:
                            if getattr(exc,'origin','platform') in {'local_queue','local_cooldown'}:
                                sent=False;self.defer(row,exc,starting=starting)
                            else:self.finish(row['id'],'rejected',str(exc)+'；未自动重发。')
                            continue
                        except Exception:
                            self.finish(row['id'],'unknown','控量提交响应未能确认，已停止后续自动提交。');continue
                    self.finish(row['id'],'active_acknowledged' if starting else 'completed_acknowledged',
                        '官方已接受创建控量请求，等待约定结束时间；API未提供独立设置回读。' if starting else '官方已接受停止控量请求；API未提供独立设置回读。',accepted=True)
                except Exception as exc:
                    if sent:
                        self.finish(row['id'],'unknown','请求或本机回执记录未完成，结果待核实；未自动重发。')
                    elif starting and getattr(exc,'reason',None) in {'rate_limited','service_busy'}:
                        self.defer(row,exc,starting=True)
                    elif stopping:
                        with self.a.db() as db:db.execute('UPDATE api_schedules SET result=? WHERE id=?',(json.dumps({'message':'结束时点读取或授权核验未完成，等待恢复；未发出停止请求。'},ensure_ascii=False),row['id']))
                    else:self.finish(row['id'],'blocked','排程执行前核对未通过：'+str(exc)[:250])
        finally:self.a.lock.release()

    def cancel(self,rid):
        with self.a.db() as db:
            cursor=db.execute("UPDATE api_schedules SET state='cancelled' WHERE id=? AND state='scheduled'",(rid,))
            return cursor.rowcount==1
