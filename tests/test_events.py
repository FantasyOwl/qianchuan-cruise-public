import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from monitor.events import normalize_event
from monitor.store import Store
from test_engine import NOW,settings


def event():
    return {'id':'reported-change','account_id':'123','entity_id':'plan1','scope':'plan','action':'budget_up',
            'metric_basis':'overall_payment','source_key':'exact-report','source':'manual_verified_report','status':'reported_applied',
            'before_at':(NOW-timedelta(hours=2,minutes=10)).isoformat(),'after_at':(NOW-timedelta(hours=2)).isoformat(),
            'before':{'budget':1000},'after':{'budget':1200},'note':'人工报告，需与快照核对'}


class OperationReportTests(unittest.TestCase):
    def test_report_drops_execution_and_credential_claims(self):
        raw=event();raw.update(execute=True,platform_verified=True,token='secret');raw['after']['api_key']='secret'
        value=normalize_event(raw,settings(),NOW)
        self.assertNotIn('secret',json.dumps(value));self.assertNotIn('execute',value);self.assertNotIn('platform_verified',value)

    def test_manual_and_simulation_status_cannot_be_relabelled(self):
        for source,status in (('simulation','reported_applied'),('manual_verified_report','simulated')):
            raw=event();raw.update(source=source,status=status)
            with self.assertRaises(ValueError):normalize_event(raw,settings(),NOW)

    def test_account_identity_and_chronology_are_explicit(self):
        for change in ({'account_id':'other'}, {'before_at':NOW.isoformat()}, {'after_at':(NOW+timedelta(hours=1)).isoformat()}, {'source_key':''}):
            raw=event();raw.update(change)
            with self.assertRaises(ValueError):normalize_event(raw,settings(),NOW)

    def test_reported_fields_are_typed_and_not_missing(self):
        for fields in ({'budget':True},{'budget':float('nan')},{'budget':1.111},{}):
            raw=event();raw['after']=fields
            with self.assertRaises(ValueError):normalize_event(raw,settings(),NOW)
        raw=event();raw.update(action='pause_plan');raw.update(before={'running':True},after={'running':'false'})
        with self.assertRaises(ValueError):normalize_event(raw,settings(),NOW)

    def test_new_task_does_not_require_an_invented_zero_old_budget(self):
        raw=event();raw.update(action='create_chase',scope='control',before={},after={'budget':100},duration_minutes=120)
        value=normalize_event(raw,settings(),NOW)
        self.assertEqual(value['before'],{});self.assertEqual(value['after']['budget'],100)

    def test_store_duplicate_is_idempotent_and_conflict_rolls_back_whole_batch(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'reports.db');one=normalize_event(event(),settings(),NOW)
            self.assertEqual(store.add_events([one]),1)
            equivalent=copy.deepcopy(one);equivalent['after']['budget']=1200.0
            self.assertEqual(store.add_events([equivalent]),0)
            two=copy.deepcopy(one);two['id']='new-id'
            conflict=copy.deepcopy(one);conflict['after']['budget']=1300
            with self.assertRaises(ValueError):store.add_events([two,conflict])
            self.assertEqual([e['id'] for e in store.events(now=NOW)],['reported-change'])

    def test_report_heads_keep_old_applied_event_even_after_a_new_rejected_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'reports.db');old=normalize_event(event(),settings(),NOW)
            old['before_at']=(NOW-timedelta(days=3,minutes=10)).isoformat();old['after_at']=(NOW-timedelta(days=3)).isoformat()
            failed=copy.deepcopy(old);failed.update(id='rejected',status='rejected',after_at=(NOW-timedelta(days=2)).isoformat())
            store.add_events([old,failed])
            self.assertEqual({e['status'] for e in store.events(now=NOW)},{'reported_applied','rejected'})
            self.assertEqual(store.events(now=NOW,account_id='other'),[])


if __name__=='__main__':unittest.main()
