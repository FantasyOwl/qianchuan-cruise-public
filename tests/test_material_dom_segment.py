"""Browser material slices cannot join delivery views or certify execution facts."""
import copy
import json
import unittest
from datetime import datetime
from pathlib import Path
from monitor.schema import normalize_snapshot


class MaterialDomSegmentTests(unittest.TestCase):
    def setUp(self):
        self.settings=json.loads((Path(__file__).resolve().parents[1]/'config.json').read_text('utf-8'))
        self.raw={'account_id':'1999999999999002','entity_id':'9000001','scope':'creative',
            'metric_basis':'overall_payment','metrics':{'spend':100,'gmv':300,'roi':3,'clicks':20},
            'source':{'kind':'browser_dom','url':'https://qianchuan.jinritemai.com/board-next?aavid=1999999999999002',
                'captured_at':'2026-10-24T07:00:00+08:00','data_at':None,'metric_date':'2026-10-24',
                'metric_period':'live_session','period_start':'2026-10-24T05:58:49+08:00','segment':'material_cost_control'},
            'control':{'history_known':False,'guarantee_verified':False,'lifecycle':'unknown'},
            'context':{'material_id':'9000001','creative_parent_verified':True,'manual_reviewed':True}}
        self.now=datetime.fromisoformat('2026-10-24T07:01:00+08:00')

    def normalize(self,row=None):
        return normalize_snapshot(row or self.raw,self.settings,self.now)

    def test_segments_and_sessions_have_distinct_series(self):
        keys=set()
        for segment in ('material_cost_control','material_volume','material_all'):
            row=copy.deepcopy(self.raw);row['source']['segment']=segment
            value=self.normalize(row);self.assertEqual(value['source']['segment'],segment)
            keys.add(value['source']['series_key'])
        self.assertEqual(len(keys),3)
        later=copy.deepcopy(self.raw);later['source']['period_start']='2026-10-24T06:58:49+08:00'
        self.assertNotIn(self.normalize(later)['source']['series_key'],keys)

    def test_capture_time_does_not_certify_freshness_or_parent(self):
        value=self.normalize()
        self.assertIsNone(value['source']['data_at']);self.assertEqual(value['source']['coverage'],'visible_only')
        self.assertEqual(value['source']['metric_period'],'live_session')
        self.assertIsNone(value['context']['creative_parent_verified']);self.assertIsNone(value['context']['manual_reviewed'])
        self.assertTrue(any('采集时间不能替代' in i for i in value['quality']['issues']))

    def test_segment_requires_board_creative_session_with_start(self):
        for change in ('scope','path','period','start'):
            row=copy.deepcopy(self.raw)
            if change=='scope':row['scope']='account'
            if change=='path':row['source']['url']=row['source']['url'].replace('board-next','uni-prom')
            if change=='period':row['source']['metric_period']='day'
            if change=='start':row['source']['period_start']=None
            with self.subTest(change=change),self.assertRaises(ValueError):self.normalize(row)
        for segment in ('unknown','',[],{},123):
            row=copy.deepcopy(self.raw);row['source']['segment']=segment
            with self.subTest(segment=segment),self.assertRaises(ValueError):self.normalize(row)

    def test_unsliced_records_keep_the_existing_source_shape(self):
        row=copy.deepcopy(self.raw);del row['source']['segment']
        value=self.normalize(row);self.assertNotIn('segment',value['source'])
        self.assertTrue(value['source']['series_key'].endswith(':unknown'))


if __name__=='__main__':unittest.main()
