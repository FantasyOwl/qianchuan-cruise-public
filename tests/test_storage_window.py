import copy
import json
import tempfile
import unittest
from collections import Counter
from datetime import timedelta
from pathlib import Path

from monitor.store import Store
from monitor.schema import validate_settings
from monitor.engine import evaluate
from test_engine import NOW,series,settings


class StorageWindowTests(unittest.TestCase):
    def test_many_objects_keep_complete_day_without_global_row_cap(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'monitor.db');rows=[]
            for minute in range(1441):
                stamp=(NOW-timedelta(minutes=minute)).isoformat()
                for entity in range(15):
                    rows.append({'id':f'{entity}:{minute}','account_id':'123','entity_id':str(entity),'scope':'creative','metric_basis':'overall_payment',
                                 'source':{'captured_at':stamp,'series_key':str(entity)},'metrics':{'spend':0}})
            store.add(rows)
            observed=store.evaluation_snapshots(now=NOW)
            self.assertEqual(len(store.snapshots()),20000)
            self.assertEqual(len(observed),21615)
            self.assertEqual(set(Counter(r['entity_id'] for r in observed).values()),{1441})

    def test_old_source_head_remains_visible_and_out_of_order_does_not_replace_it(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'monitor.db')
            row=series()[-1];row['source']['captured_at']=(NOW-timedelta(days=3)).isoformat();row['id']='latest'
            older=copy.deepcopy(row);older['source']['captured_at']=(NOW-timedelta(days=4)).isoformat();older['id']='older'
            store.add([row,older])
            self.assertEqual([r['id'] for r in store.evaluation_snapshots(now=NOW)],['latest'])
            store=Store(Path(folder)/'monitor.db')
            self.assertEqual([r['id'] for r in store.evaluation_snapshots(now=NOW)],['latest'])

    def test_history_filter_does_not_mix_accounts_or_series(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'monitor.db');a=series()[-1];b=copy.deepcopy(a)
            b['id']='other';b['source']['series_key']='other'
            store.add([a,b])
            self.assertEqual(len(store.evaluation_snapshots(now=NOW,account_id='123',series_key='report1')),1)
            self.assertEqual(store.evaluation_snapshots(now=NOW,account_id='999'),[])


class StrategyFidelityTests(unittest.TestCase):
    def test_budget_decrease_range_preserves_ten_percent_minimum(self):
        with self.assertRaises(ValueError):validate_settings({'rules':{'budget_step_down':.01}})
        rows=series();cfg=settings();cfg['rules']['budget_step_down']=.01
        self.assertEqual(next(r for r in evaluate(rows,cfg,NOW)['recommendations'] if r['action']=='budget_down')['change_pct'],-.1)

    def test_chengfang_is_known_but_guarantee_not_implicitly_granted(self):
        rows=series(roi=2.5)
        for row in rows:row['promotion_type']='chengfang'
        rec=next(r for r in evaluate(rows,settings(),NOW)['recommendations'] if r['action']=='budget_up')
        self.assertTrue(rec['eligible'])
        rows[-1]['control']['guarantee_verified']=False
        rec=next(r for r in evaluate(rows,settings(),NOW)['recommendations'] if r['action']=='budget_up')
        self.assertFalse(rec['eligible'])

    def test_low_hourly_control_roi_does_not_mean_no_improvement(self):
        rows=series(scope='control',basis='control_payment')
        rows[-1]['metrics']['control_gmv']+=200
        result=evaluate(rows,settings('control_payment'),NOW)
        rec=next(r for r in result['recommendations'] if r['rule']=='control_roi_low_1h')
        self.assertFalse(rec['eligible']);self.assertFalse(rec['evidence']['no_improvement_1h'])
        rows[-1]['metrics']['control_gmv']-=200
        rec=next(r for r in evaluate(rows,settings('control_payment'),NOW)['recommendations'] if r['rule']=='control_roi_low_1h')
        self.assertTrue(rec['eligible']);self.assertTrue(rec['evidence']['no_improvement_1h'])


if __name__=='__main__':unittest.main()
