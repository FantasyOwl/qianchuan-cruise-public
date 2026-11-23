"""Temporary deadline storage replay; never instantiates a native IO client."""
from datetime import timedelta
from pathlib import Path
import tempfile

from .control_deadline import DeadlineLedger
from .native_control_io import digest
from .schema import CN
from datetime import datetime


def deadline_example():
    # Marker digest is explicitly not a validated material creation contract.
    at=datetime.now(CN).replace(hour=16,minute=0,second=0,microsecond=0)
    with tempfile.TemporaryDirectory(prefix='qianchuan-deadline-demo-') as temp:
        path=Path(temp)/'synthetic.db';ledger=DeadlineLedger(path)
        record=ledger.reserve('synthetic-deadline','9990001','7333333333333333','material_chase','10001',2,
            digest('synthetic pending creation contract; no native request'),at)
        ledger=DeadlineLedger(path);reopened=ledger.get(record['id'])
        timeline=[{'at':at.isoformat(),'state':record['state'],'due':False,'note':'在临时库预留2小时截止；尚无创建合同或任务ID'},
            {'at':(at+timedelta(hours=1)).isoformat(),'state':reopened['state'],'due':False,'note':'重开临时库后保留原截止时间'},
            {'at':(at+timedelta(hours=2)).isoformat(),'state':'creation_identity_unknown','due':True,'note':'到期但没有原生任务身份，保持占用，不按名称寻找或发送结束'}]
    return {'synthetic':True,'account_id':record['account_id'],'parent_plan_id':record['parent_plan_id'],
        'material_id':record['material_id'],'task_id':None,'duration_hours':2,'due_at':record['due_at'],
        'timeline':timeline,'temporary_database_removed':True,'executed':False,'persisted':False,
        'native_client_instantiated':False,'native_url':None,'creation_contract_validated':False,
        'material_creation_still_blocked':True,'note':'只演示临时持久截止与身份不足；完整原生关停链经独立模拟HTTP测试，真实创建和调度未接通。'}
