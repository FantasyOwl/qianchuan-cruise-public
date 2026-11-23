"""Synthetic provider and durable replay; no platform or network interaction."""
import copy
import tempfile
from datetime import timedelta
from pathlib import Path

from .creative_rotation import preview_creative_rotations
from .creative_workflow import WorkflowLedger,run_synthetic_step
from .demo import creative_rotation_scenario
from .evaluation import evaluate_all
from .schema import parse_time


class SyntheticMaterialProvider:
    synthetic_only=True

    def __init__(self,settings):
        self.rows,self.cfg,self.at,self.registry=creative_rotation_scenario(settings)
        self.added=False;self.retired=False;self.added_at=None;self.calls=[]

    def frame(self,now):
        rows=copy.deepcopy(self.rows);cfg=copy.deepcopy(self.cfg);payload=copy.deepcopy(self.registry)
        shift=now-self.at
        for row in rows:
            for k in ('captured_at','data_at'):
                row['source'][k]=(parse_time(row['source'][k])+shift).isoformat()
            row['source']['metric_date']=parse_time(row['source']['data_at']).date().isoformat()
            if self.added_at:row['control'].update(last_change_at=self.added_at.isoformat(),changes_today=1)
        plan=payload['plans'][0];plan['observed_at']=now.isoformat()
        plan['pool'][0]['reviewed_at']=now.isoformat();payload['duplicate_inventory'][0]['observed_at']=now.isoformat()
        if self.added:
            new=copy.deepcopy(plan['slots'][0]);new.update(snapshot_id=None,material_id='7333333333333333',
                native=copy.deepcopy(plan['pool'][0]['native']),duplicate_group_id='demo-new-group',
                available=True,status_at=now.isoformat(),status_verified=True,status_source='合成独立配置状态')
            plan['slots'].append(new);plan['capacity']['active_count']=2
            payload['duplicate_inventory'][0]['groups'][1]['active_count']=1
        if self.retired:
            plan['slots']=plan['slots'][1:];plan['capacity']['active_count']=1
            payload['duplicate_inventory'][0]['groups'][0]['active_count']=0
        return rows,cfg,payload

    def mutate(self,stage,workflow,now):
        self.calls.append(stage)
        assert workflow['synthetic_only'] is True
        assert workflow['draft']['requests'][stage]['url'] is None
        if stage=='add':self.added=True;self.added_at=now
        else:self.retired=True
        return {'acknowledged':True,'synthetic_only':True}

    def readback(self,workflow,stage,now):
        return {'synthetic_only':True,'workflow_id':workflow['id'],'account_id':workflow['account_id'],
            'plan_id':workflow['plan_id'],'native':copy.deepcopy(workflow['draft']['selection']['new_native' if stage=='add' else 'old_native']),
            'product_id':workflow['draft']['selection'].get('product_id'),'aweme_uid':workflow['draft']['selection'].get('aweme_uid'),
            'present':stage=='add','replacement_native':copy.deepcopy(workflow['draft']['selection']['new_native']),
            'observed_at':now.isoformat(),'complete_inventory':True,
            'matched':self.added if stage=='add' else self.retired,'usable':self.added}

    def observations(self,workflow,now):
        start=parse_time(workflow['retire_confirmed_at']);clocks=[];at=start
        while at<=now:clocks.append(at);at+=timedelta(minutes=10)
        if clocks[-1]!=now:clocks.append(now)
        return [{'synthetic_only':True,'account_id':workflow['account_id'],'plan_id':workflow['plan_id'],
            'workflow_id':workflow['id'],'product_id':workflow['draft']['selection'].get('product_id'),
            'aweme_uid':workflow['draft']['selection'].get('aweme_uid'),
            'native':copy.deepcopy(workflow['draft']['selection']['new_native']),'available':self.added,
            'observed_at':at.isoformat()} for at in clocks]


def creative_workflow_replay(settings):
    adapter=SyntheticMaterialProvider(settings);now=adapter.at
    rows,cfg,payload=adapter.frame(now)
    preview=preview_creative_rotations(payload,rows,cfg,evaluate_all(rows,cfg,now),now);draft=preview['drafts'][0]
    timeline=[]
    with tempfile.TemporaryDirectory(prefix='qianchuan-synthetic-workflow-') as temp:
        path=Path(temp)/'workflow.db';ledger=WorkflowLedger(path)
        record=ledger.create(preview,draft['account_id'],draft['plan_id'],draft['intent_digest'],now)
        workflow_id=record['id'];timeline.append({'at':now.isoformat(),'state':record['state'],'version':record['version']})
        # Reopen SQLite before every step, including the waits and observation.
        for seconds in (0,2,3601,3602,3603,3604,10803,10804):
            at=now+timedelta(seconds=seconds);ledger=WorkflowLedger(path)
            record=run_synthetic_step(ledger,workflow_id,adapter,at)
            timeline.append({'at':at.isoformat(),'state':record['state'],'version':record['version']})
        journal=ledger.events(workflow_id)
        held=record['parent_reservation_held'] or record['group_reservation_held']
    return {'synthetic_only':True,'persisted':False,'temporary_durable_storage_verified':True,
            'reopened_before_every_step':True,'production_workflows_changed':False,
            'workflow':record,'timeline':timeline,'journal':journal,'synthetic_calls':adapter.calls,
            'holds_remain':held,'executed':False,'real_ad_actions':0,'platform_verified':False,
            'native_material_submission_enabled':False,
            'note':'阶段保存在临时SQLite并逐步重新打开；读回、样本及动作全部合成，临时库已清理，不代表真实替换完成。'}
