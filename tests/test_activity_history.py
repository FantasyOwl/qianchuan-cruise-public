import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from monitor.official_actions import OfficialActions


class ActivityHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.aid='1999999999999002'
        self.other='1999999999999001'
        self.transport=Mock(side_effect=AssertionError('History must not call the platform'))
        accounts=[{'id':self.aid,'observe':False},{'id':self.other}]
        self.queue=OfficialActions(Path(self.tmp.name)/'actions.db',
            SimpleNamespace(transport=self.transport),lambda:accounts,Mock(),writer=self.transport)

    def insert(self,rid,*,aid=None,action='task_budget',state='verified',day='2026-11-10',**extra):
        intent={'account_id':aid or self.aid,'plan_id':'8218616704149013',
            'task_id':'8533524212032260758','action':action,'before':'500','after':'600',**extra}
        stamp=day+'T09:10:00+08:00'
        with self.queue.db() as db:
            db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',
                (rid,state,json.dumps(intent,ensure_ascii=False),'digest',stamp,
                 '2026-01-01T00:00:00+08:00',stamp,json.dumps({'task_id':'8533524212032260758'})))

    def test_paging_reads_beyond_global_200_without_expiring_drafts_or_network_calls(self):
        for i in range(43):self.insert(f'local-{i:03}',day='2026-11-09')
        self.insert('draft',state='draft',day='2026-11-08')
        for i in range(230):self.insert(f'other-{i:03}',aid=self.other)
        first=self.queue.activity_history({'account_id':self.aid})
        self.assertEqual((first['total'],first['page'],first['pages']),(44,1,3))
        second=self.queue.activity_history({'account_id':self.aid,'page':'2'})
        last=self.queue.activity_history({'account_id':self.aid,'page':'99'})
        ids=[r['id'] for page in (first,second,last) for r in page['records']]
        self.assertEqual(len(set(ids)),44)
        self.assertEqual(last['page'],3)
        self.assertTrue(all(r['intent']['account_id']==self.aid for page in (first,second,last) for r in page['records']))
        self.assertEqual(next(r for r in last['records'] if r['id']=='draft')['state'],'draft')
        self.transport.assert_not_called()

    def test_filters_are_combined_and_search_keeps_long_ids_and_literal_wildcards(self):
        self.insert('one',plan_name='A%_计划',material_ids=['8795113841310834555'])
        self.insert('two',action='pause_task')
        self.insert('three',state='unknown')
        self.insert('four',day='2026-11-09')
        self.insert('five',aid=self.other,plan_name='A%_计划')
        result=self.queue.activity_history({'account_id':self.aid,'action':'budget',
            'state':'verified','day':'2026-11-10','keyword':'%_'})
        self.assertEqual([r['id'] for r in result['records']],['one'])
        found=self.queue.activity_history({'account_id':self.aid,'keyword':'8795113841310834555'})
        self.assertEqual(found['total'],1)
        self.assertEqual(found['records'][0]['intent']['material_ids'],['8795113841310834555'])
        pending=self.queue.activity_history({'account_id':self.aid,'state':'pending'})
        self.assertEqual([r['id'] for r in pending['records']],['three'])
        self.transport.assert_not_called()

    def test_followup_pause_status_is_included_only_for_this_account_and_page(self):
        self.insert('created',action='material_chase')
        self.insert('other-created',aid=self.other,action='material_chase')
        with self.queue.db() as db:
            db.execute('INSERT INTO api_followups VALUES(?,?,?,?,?,?,?,?)',
                ('stop','created',self.aid,'8218616704149013','8533524212032260758',
                 '2026-11-10T12:00:00+08:00','verified',json.dumps({'message':'已核对原任务停止'})))
            db.execute('INSERT INTO api_followups VALUES(?,?,?,?,?,?,?,?)',
                ('foreign-stop','created',self.other,'8218616704149013','123',
                 '2026-11-10T12:00:00+08:00','unknown',None))
        rows=self.queue.activity_history({'account_id':self.aid})['records']
        self.assertEqual([f['id'] for f in rows[0]['followups']],['stop'])
        self.assertEqual(rows[0]['followups'][0]['result']['message'],'已核对原任务停止')

    def test_invalid_or_unregistered_queries_are_rejected(self):
        for change in ({'account_id':'999'},{'page':'0'},{'page':'1.5'},{'page_size':'1000'},
                {'state':'submitting'},{'action':'raw_write'},{'day':'2026-02-30'},
                {'day':'20261110'},{'keyword':'x'*101},{'sql':'1=1'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.queue.activity_history({'account_id':self.aid,**change})
        self.transport.assert_not_called()


if __name__=='__main__':unittest.main()
