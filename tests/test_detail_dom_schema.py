"""The same child ID under separate plans must not join a performance series."""
import copy
import json
import unittest
from datetime import datetime
from pathlib import Path
from monitor.schema import normalize_snapshot,normalize_context


class DetailDomSchemaTests(unittest.TestCase):
    def setUp(self):
        self.settings=json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text('utf-8'))
        self.now=datetime.fromisoformat('2026-10-24T08:00:00+08:00')
        self.raw={'account_id':'1999999999999001','entity_id':'8339456325564332585','scope':'product',
            'metric_basis':'marketing','metrics':{'spend':100,'comprehensive_cost':100,'marketing_roi':3,'net_gmv':300},
            'source':{'kind':'browser_dom','url':'https://qianchuan.jinritemai.com/overall-prom?aavid=1999999999999001',
                'captured_at':'2026-10-24T07:30:00+08:00','data_at':None,'metric_date':'2026-10-24',
                'metric_period':'day','parent_entity_id':'9000001'},
            'context':{'parent_plan_id':'9000001','product_plan_mapping_verified':True},'control':{}}

    def normalize(self,row=None):
        return normalize_snapshot(row or self.raw,self.settings,self.now)

    def test_same_product_under_other_plans_has_distinct_series(self):
        first=self.normalize();other=copy.deepcopy(self.raw)
        other['source']['parent_entity_id']=other['context']['parent_plan_id']='9000002'
        second=self.normalize(other)
        self.assertNotEqual(first['source']['series_key'],second['source']['series_key'])
        self.assertEqual(first['source']['parent_entity_id'],'9000001')
        self.assertIsNone(first['context']['product_plan_mapping_verified'])
        self.assertIsNone(first['source']['data_at'])

    def test_mismatched_malformed_or_wrong_scope_parent_rejected(self):
        for mode in ('context','scope','number','list','missing_context'):
            row=copy.deepcopy(self.raw)
            if mode=='context':row['context']['parent_plan_id']='9000002'
            if mode=='scope':row['scope']='account'
            if mode=='number':row['source']['parent_entity_id']=9000001
            if mode=='list':row['source']['parent_entity_id']=[]
            if mode=='missing_context':row['context']={}
            with self.subTest(mode=mode),self.assertRaises(ValueError):self.normalize(row)

    def test_control_targets_stay_separate_and_dom_does_not_authenticate_state(self):
        row=copy.deepcopy(self.raw);row.update(scope='control',metric_basis='control_payment',account_id='1999999999999002')
        row['metrics']={'spend':100,'roi':2.5,'gmv':250,'net_roi':2,'net_roi_goal':2.9,'payment_roi_goal':3.5}
        row['source']['url']='https://qianchuan.jinritemai.com/uni-prom/detail?aavid=1999999999999002'
        row['context'].update(observed_duration_mode='unlimited',control_delivery_mode='cost_control')
        value=self.normalize(row)
        self.assertEqual(value['metrics']['net_roi_goal'],2.9);self.assertEqual(value['metrics']['payment_roi_goal'],3.5)
        self.assertNotIn('target_roi',value['metrics']);self.assertNotIn('observed_duration_hours',value['context'])
        self.assertFalse(value['control']['history_known']);self.assertFalse(value['control']['guarantee_verified'])

    def test_observed_duration_requires_explicit_positive_fixed_mode(self):
        self.assertEqual(normalize_context({'observed_duration_mode':'fixed','observed_duration_hours':2.5},'browser_dom')['observed_duration_hours'],2.5)
        for raw in ({'observed_duration_mode':[]},{'observed_duration_mode':{}},{'control_delivery_mode':[]},
                    {'observed_duration_mode':'unlimited','observed_duration_hours':2},
                    {'observed_duration_mode':'fixed','observed_duration_hours':0}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):normalize_context(raw,'browser_dom')


if __name__=='__main__':unittest.main()
