"""Durable rotation coordination. Native adapters are deliberately unavailable.

The observation server can save a freshly qualified draft, never drive it.
The implemented runner accepts synthetic workflows/providers only. Its journal,
claims and shared reservations survive reopening; unknown IO is never retried.
"""
import copy
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .creative_rotation import preview_creative_rotations
from .creative_retirement import review_retirement
from .evaluation import evaluate_all
from .official_reports import _guard
from .plan_locks import check_attempts,check_reservations
from .schema import parse_time

TERMINAL={'completed_synthetic','cancelled_before_add'}
HELD={'add_claimed','retire_claimed','add_unknown','retire_unknown'}
TRANSITIONS={
    'ready_add':{'ready_add','add_claimed','cancelled_before_add'},
    'awaiting_native_integration':{'cancelled_before_add'},
    'add_claimed':{'verify_add','add_unknown'},'verify_add':{'cooldown'},
    'cooldown':{'ready_retire'},'ready_retire':{'ready_retire','retire_claimed'},
    'retire_claimed':{'verify_retire','retire_unknown'},'verify_retire':{'observe'},
    'observe':{'completed_synthetic'},
}


def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def _time(value):
    if not isinstance(value,str):raise ValueError('流程时间无效')
    return parse_time(value)


def qualified_draft(preview,account,plan,intent):
    _guard(preview)
    if not isinstance(intent,str) or len(intent)!=64:raise ValueError('缺少当前具体替换意图摘要')
    matches=[d for d in preview.get('drafts',[]) if d['account_id']==account and d['plan_id']==plan]
    if len(matches)!=1 or matches[0].get('local_eligible') is not True or matches[0].get('intent_digest')!=intent:
        raise ValueError('当前完整评估没有相同的合格素材替换意图，请重新预览')
    draft=matches[0]
    if draft.get('executed') is not False or draft.get('execution_eligible') is not False:
        raise ValueError('本地流程只接受未执行、未获执行资格的草稿')
    return copy.deepcopy(draft)


class WorkflowLedger:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS creative_workflows(
                    id TEXT PRIMARY KEY,account_id TEXT NOT NULL,plan_id TEXT NOT NULL,
                    intent_digest TEXT NOT NULL,updated_at TEXT NOT NULL,version INTEGER NOT NULL,data_json TEXT NOT NULL);
                CREATE UNIQUE INDEX IF NOT EXISTS creative_workflow_intent ON creative_workflows(account_id,plan_id,intent_digest);
                CREATE TABLE IF NOT EXISTS creative_workflow_events(
                    workflow_id TEXT NOT NULL,seq INTEGER NOT NULL,created_at TEXT NOT NULL,event TEXT NOT NULL,
                    from_state TEXT,to_state TEXT NOT NULL,details_json TEXT NOT NULL,PRIMARY KEY(workflow_id,seq));
                CREATE TABLE IF NOT EXISTS action_reservations(
                    owner_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,plan_id TEXT NOT NULL,created_at TEXT NOT NULL,released_at TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS active_parent_reservation ON action_reservations(account_id,plan_id) WHERE released_at IS NULL;
                CREATE TABLE IF NOT EXISTS creative_group_reservations(
                    owner_id TEXT PRIMARY KEY,account_id TEXT NOT NULL,group_id TEXT NOT NULL,quantity INTEGER NOT NULL,released_at TEXT);
            ''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(str(self.path),timeout=5)
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def existing(path,*,with_journal=False):
        """Read only, including when no workflow database has ever been made."""
        path=Path(path)
        if not path.exists():return []
        db=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True)
        try:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='creative_workflows'").fetchone():return []
            records=[json.loads(r[0]) for r in db.execute('SELECT data_json FROM creative_workflows ORDER BY updated_at DESC')]
            if with_journal:
                for record in records:
                    record['journal']=[{'seq':s,'at':at,'event':event,'from_state':a,'to_state':b,'details':json.loads(details)}
                        for s,at,event,a,b,details in db.execute('SELECT seq,created_at,event,from_state,to_state,details_json FROM creative_workflow_events WHERE workflow_id=? ORDER BY seq',(record['id'],))]
            return records
        finally:db.close()

    def get(self,workflow_id):
        with self.connect() as db:r=db.execute('SELECT data_json FROM creative_workflows WHERE id=?',(workflow_id,)).fetchone()
        if not r:raise ValueError('找不到本地素材流程')
        return json.loads(r[0])

    def events(self,workflow_id):
        with self.connect() as db:
            return [{'seq':s,'at':at,'event':event,'from_state':a,'to_state':b,'details':json.loads(details)}
                    for s,at,event,a,b,details in db.execute('SELECT seq,created_at,event,from_state,to_state,details_json FROM creative_workflow_events WHERE workflow_id=? ORDER BY seq',(workflow_id,))]

    def create(self,preview,account,plan,intent,now):
        """Caller supplies a trusted, freshly recomputed full-server preview."""
        draft=qualified_draft(preview,account,plan,intent)
        if not 0<=(now-_time(preview['checked_at'])).total_seconds()<=5:raise ValueError('流程创建需同次新鲜评估')
        identity=account,plan
        workflow_id='cw_'+hashlib.sha256(_json([account,plan,intent]).encode()).hexdigest()[:24]
        minimum=next(s['minimum_seconds'] for s in draft['stages'] if s['action']=='cooldown')
        group=draft['selection']['declared_duplicate_group'];baseline=draft['selection']['declared_group_count_before']
        record={'id':workflow_id,'account_id':account,'plan_id':plan,'intent_digest':intent,
                'preview_digest':preview['preview_digest'],'draft':draft,'created_at':now.isoformat(),'updated_at':now.isoformat(),
                'state':'ready_add' if draft['synthetic_only'] else 'awaiting_native_integration','version':1,
                'minimum_cooldown_seconds':minimum,'minimum_observation_seconds':7200,
                'synthetic_only':draft['synthetic_only'],'executed':False,'platform_verified':False,
                'real_ad_actions':0,'simulation_action_count':0,'native_adapter_available':False,
                'automatic_retry_allowed':False,'parent_reservation_held':True,'group_reservation_held':True,
                'last_blockers':[]}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior=db.execute('SELECT data_json FROM creative_workflows WHERE id=?',(workflow_id,)).fetchone()
            if prior:return json.loads(prior[0])
            check_reservations(db,identity);check_attempts(db,identity,now,minimum)
            reserved=db.execute('SELECT COALESCE(SUM(quantity),0) FROM creative_group_reservations WHERE account_id=? AND group_id=? AND released_at IS NULL',(account,group)).fetchone()[0]
            if type(baseline) is not int or baseline<0 or baseline+reserved+1>3:
                raise ValueError('计入持久预留后，账户重复素材将超过3条')
            db.execute('INSERT INTO action_reservations VALUES(?,?,?,?,NULL)',(workflow_id,account,plan,now.isoformat()))
            db.execute('INSERT INTO creative_group_reservations VALUES(?,?,?,?,NULL)',(workflow_id,account,group,1))
            db.execute('INSERT INTO creative_workflows VALUES(?,?,?,?,?,?,?)',(workflow_id,account,plan,intent,now.isoformat(),1,_json(record)))
            db.execute('INSERT INTO creative_workflow_events VALUES(?,?,?,?,?,?,?)',(workflow_id,1,now.isoformat(),'created',None,record['state'],_json({'native_adapter_available':False,'synthetic_only':record['synthetic_only']})))
        return record

    def change(self,old,state,event,now,updates=None,details=None,release=False):
        """One compare-and-swap transaction also journals and releases holds."""
        if now<_time(old['updated_at']):raise ValueError('流程时钟不能倒退')
        if state not in TRANSITIONS.get(old['state'],set()):raise ValueError('流程阶段不允许该跳转，保持原有预留')
        if set(updates or {})-{'last_blockers','simulation_action_count','add_attempt_at','retire_attempt_at','add_confirmed_at','retire_confirmed_at'}:
            raise ValueError('流程身份和执行边界不能由阶段更新修改')
        new={**old,**(updates or {}),'state':state,'updated_at':now.isoformat(),'version':old['version']+1}
        if release:
            if state not in TERMINAL:raise ValueError('未知或运行中流程不能解除预留')
            new.update(parent_reservation_held=False,group_reservation_held=False)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT data_json FROM creative_workflows WHERE id=?',(old['id'],)).fetchone()
            if current is None or json.loads(current[0])!=old:raise ValueError('流程阶段已改变或调用方记录不一致')
            changed=db.execute('UPDATE creative_workflows SET updated_at=?,version=?,data_json=? WHERE id=? AND version=?',
                (new['updated_at'],new['version'],_json(new),old['id'],old['version'])).rowcount
            if changed!=1:raise ValueError('流程阶段已由其他调用改变，请读取当前进度')
            db.execute('INSERT INTO creative_workflow_events VALUES(?,?,?,?,?,?,?)',
                (old['id'],new['version'],new['updated_at'],event,old['state'],state,_json(details or {})))
            if release:
                db.execute('UPDATE action_reservations SET released_at=? WHERE owner_id=?',(new['updated_at'],old['id']))
                db.execute('UPDATE creative_group_reservations SET released_at=? WHERE owner_id=?',(new['updated_at'],old['id']))
        return new

    def cancel_before_add(self,workflow_id,now):
        old=self.get(workflow_id)
        if old['state'] not in {'ready_add','awaiting_native_integration'}:raise ValueError('已开始添加或结果未知，不能靠取消解除占用')
        return self.change(old,'cancelled_before_add','cancelled_before_any_attempt',now,release=True)


def _fresh_preview(adapter,now):
    rows,cfg,payload=adapter.frame(now)
    evaluation=evaluate_all(rows,cfg,now)
    return preview_creative_rotations(payload,rows,cfg,evaluation,now)


def _same_intent(a,b):
    keys=('material_type','old_material_id','old_native','new_native','product_id','aweme_uid','weak_created_at')
    return a['account_id']==b['account_id'] and a['plan_id']==b['plan_id'] and a['scene']==b['scene'] and a['requests']==b['requests'] and all(a['selection'].get(k)==b['selection'].get(k) for k in keys)


def _readback_matches(evidence,workflow,stage,now):
    if not isinstance(evidence,dict):return False
    try:
        at=_time(evidence.get('observed_at'));claimed=_time(workflow[stage+'_attempt_at'])
        selected=workflow['draft']['selection'];expected=selected['new_native' if stage=='add' else 'old_native']
        return (evidence.get('synthetic_only') is True and evidence.get('account_id')==workflow['account_id']
            and evidence.get('plan_id')==workflow['plan_id'] and evidence.get('workflow_id')==workflow['id']
            and evidence.get('product_id')==selected.get('product_id') and evidence.get('aweme_uid')==selected.get('aweme_uid')
            and evidence.get('present') is (stage=='add') and evidence.get('replacement_native')==selected['new_native']
            and evidence.get('native')==expected and evidence.get('complete_inventory') is True
            and evidence.get('matched') is True and evidence.get('usable') is True
            and claimed<=at<=now and (now-at).total_seconds()<=120)
    except (ValueError,TypeError,KeyError):return False


def run_synthetic_step(ledger,workflow_id,adapter,now):
    """Drive one durable step. No native provider or real workflow is accepted."""
    old=ledger.get(workflow_id)
    if old['synthetic_only'] is not True or getattr(adapter,'synthetic_only',False) is not True:
        raise PermissionError('原生素材适配器尚未接通；当前运行器只能推进合成流程')
    if now<_time(old['updated_at']):raise ValueError('流程时钟不能倒退')
    state=old['state']
    if state in TERMINAL or state in HELD:return old
    if state=='cooldown':
        if (now-_time(old['add_confirmed_at'])).total_seconds()<old['minimum_cooldown_seconds']:return old
        return ledger.change(old,'ready_retire','cooldown_elapsed_requires_fresh_rebuild',now)
    if state in {'ready_add','ready_retire'}:
        stage='add' if state=='ready_add' else 'retire'
        if stage=='add':
            preview=_fresh_preview(adapter,now)
            draft=next((d for d in preview['drafts'] if d['account_id']==old['account_id'] and d['plan_id']==old['plan_id']),None)
            blockers=[] if draft and draft['local_eligible'] and _same_intent(draft,old['draft']) else ['添加前的新鲜完整评估不再支持相同替换意图']
        else:
            rows,cfg,payload=adapter.frame(now)
            review=review_retirement(old['draft'],payload,rows,cfg,evaluate_all(rows,cfg,now),now)
            blockers=review['blockers']
        if blockers:return ledger.change(old,state,'fresh_review_blocked',now,{'last_blockers':blockers})
        claimed=ledger.change(old,stage+'_claimed',stage+'_claimed_before_io',now,
            {stage+'_attempt_at':now.isoformat(),'last_blockers':[],'simulation_action_count':old['simulation_action_count']+1})
        # A crash after this durable claim remains held; reopening cannot resubmit.
        try:ack=adapter.mutate(stage,copy.deepcopy(claimed),now)
        except Exception:ack=None
        accepted=isinstance(ack,dict) and ack.get('acknowledged') is True and ack.get('synthetic_only') is True
        return ledger.change(claimed,'verify_'+stage if accepted else stage+'_unknown',
            stage+'_acknowledged_requires_readback' if accepted else stage+'_outcome_unknown',now)
    if state in {'verify_add','verify_retire'}:
        stage='add' if state=='verify_add' else 'retire'
        try:evidence=adapter.readback(copy.deepcopy(old),stage,now)
        except Exception:return old
        if not _readback_matches(evidence,old,stage,now):return old
        return ledger.change(old,'cooldown' if stage=='add' else 'observe',stage+'_independently_matched_in_simulation',now,
            {stage+'_confirmed_at':evidence['observed_at'],'last_blockers':[]},
            {'synthetic_only':True,'complete_inventory':True,'observed_at':evidence['observed_at']})
    if state=='observe':
        start=_time(old['retire_confirmed_at'])
        if (now-start).total_seconds()<old['minimum_observation_seconds']:return old
        try:points=adapter.observations(copy.deepcopy(old),now)
        except Exception:return old
        if not isinstance(points,list) or len(points)<13:return old
        try:
            clocks=[_time(p['observed_at']) for p in points]
            valid=all(p.get('synthetic_only') is True and p.get('account_id')==old['account_id']
                and p.get('plan_id')==old['plan_id'] and p.get('workflow_id')==old['id']
                and p.get('product_id')==old['draft']['selection'].get('product_id')
                and p.get('aweme_uid')==old['draft']['selection'].get('aweme_uid')
                and p.get('native')==old['draft']['selection']['new_native']
                and p.get('available') is True for p in points)
            valid=valid and clocks[0]==start and clocks[-1]<=now and (now-clocks[-1]).total_seconds()<=120
            valid=valid and (clocks[-1]-clocks[0]).total_seconds()>=old['minimum_observation_seconds']
            valid=valid and all(0<(b-a).total_seconds()<=1200 for a,b in zip(clocks,clocks[1:]))
        except (ValueError,TypeError,KeyError):return old
        if not valid:return old
        return ledger.change(old,'completed_synthetic','synthetic_observation_completed',now,
            {'last_blockers':[]}, {'observations':len(points),'observed_start':clocks[0].isoformat(),'observed_end':clocks[-1].isoformat()},release=True)
    raise ValueError('素材流程阶段未知，保持占用并等待核对')
