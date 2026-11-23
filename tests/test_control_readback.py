import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from monitor.control_readback import (ControlTaskReadClient,ControlTaskReadPermit,
    prepare_task_read_request,compare_supplied_task_pages,_get_task_page)
from monitor.plan_detail import PlanDetailPermit
from monitor.submission import AttemptLedger, _post_control_status
from test_engine import NOW
from test_control_submission import PARENT,TASK


CFG={'accounts':[{'id':'123','scene':'live','observe':True}]}
START=NOW-timedelta(hours=1)
END=NOW-timedelta(minutes=1)


def page(rows,number,total,size=10):
    return json.dumps({'code':0,'data':{'task_list':rows,'page_info':{
        'page':number,'page_size':size,'total_number':total,'total_page':(total+size-1)//size}}})


def inputs(pause=False):
    rows=[{'id':n,'budget':500,'task_status':'PROCESSING','name':'private-task-name'} for n in range(1,12)]
    rows.append({'id':int(TASK),'budget':500,'task_status':'PROCESSING'})
    pages=[page(rows[:10],1,12),page(rows[10:],2,12)]
    request=prepare_task_read_request('123',PARENT,TASK,'material_chase',START,END,CFG,max_pages=2,page_size=10)
    q={k:request[k] for k in ('account_id','parent_plan_id','marketing_goal','scene','task_id','window_start','window_end','max_pages','page_size')}
    q.update(request_started_at=(NOW-timedelta(seconds=30)).isoformat(),received_at=(NOW-timedelta(seconds=20)).isoformat())
    attempt={'created_at':(NOW-timedelta(minutes=5)).isoformat(),'context':{
        'account_id':'123','entity_id':TASK,'scope':'control','parent_plan_id':PARENT,'control_type':'material_chase',
        'marketing_goal':'LIVE_PROM_GOODS','action':'pause_control' if pause else 'control_budget_up',
        'before':True if pause else 400,'after':False if pause else 500}}
    return pages,q,attempt,request


class TaskPageComparisonTests(unittest.TestCase):
    def test_target_on_second_page_compares_budget_without_claiming_complete_inventory(self):
        pages,q,a,_=inputs();r=compare_supplied_task_pages(pages,q,a,NOW)
        self.assertEqual(r['status'],'matches_requested_value');self.assertEqual(r['observed'],'500.00')
        self.assertTrue(r['pagination_exhausted']);self.assertEqual(r['pages_received'],2)
        for field in ('inventory_complete','provenance_verified','platform_state_observed','execution_confirmed','can_release_hold','automatic_retry'):
            self.assertFalse(r[field])
        self.assertNotIn('private-task-name',json.dumps(r))

    def test_first_page_match_cannot_override_unread_second_page(self):
        pages,q,a,_=inputs();r=json.loads(pages[0]);r['data']['task_list'][0]['id']=int(TASK)
        q['max_pages']=1
        result=compare_supplied_task_pages([json.dumps(r)],q,a,NOW)
        self.assertEqual(result['status'],'inconclusive');self.assertFalse(result['pagination_exhausted'])
        self.assertEqual(result['observed'],'500.00')

    def test_count_drift_page_reordering_and_cross_page_duplicate_ids_are_rejected(self):
        pages,q,a,_=inputs()
        variants=[pages[::-1]]
        second=json.loads(pages[1]);second['data']['page_info']['total_number']=13
        variants.append([pages[0],json.dumps(second)])
        second=json.loads(pages[1]);second['data']['task_list'][0]['id']=1
        variants.append([pages[0],json.dumps(second)])
        for bad in variants:
            with self.assertRaises(ValueError):compare_supplied_task_pages(bad,q,a,NOW)

    def test_absent_filtered_task_is_not_failed_or_deleted_even_empty_query(self):
        _,q,a,_=inputs();result=compare_supplied_task_pages([page([],1,0)],q,a,NOW)
        self.assertEqual(result['status'],'inconclusive');self.assertTrue(result['pagination_exhausted'])
        self.assertIsNone(result['observed']);self.assertIn('不能推断',result['blockers'][0])

    def test_name_or_substring_match_and_float_id_do_not_replace_exact_task_id(self):
        pages,q,a,_=inputs();rows=json.loads(pages[1]);rows['data']['task_list'][-1].update(id=99,name=TASK)
        self.assertIsNone(compare_supplied_task_pages([pages[0],json.dumps(rows)],q,a,NOW)['observed'])
        rows['data']['task_list'][-1]['id']=float(TASK)
        with self.assertRaises(ValueError):compare_supplied_task_pages([pages[0],json.dumps(rows)],q,a,NOW)

    def test_documented_non_delivery_statuses_do_not_certify_a_pause(self):
        pages,q,a,_=inputs(True)
        for state in ('PROCESSING','DISABLE','FROZEN','ROI2_DISABLE','OFFLINE_BALANCE','PAUSE'):
            last=json.loads(pages[1]);last['data']['task_list'][-1]['task_status']=state
            result=compare_supplied_task_pages([pages[0],json.dumps(last)],q,a,NOW)
            self.assertEqual(result['status'],'inconclusive');self.assertFalse(result['can_release_hold'])
            self.assertIn('未定义PAUSE',''.join(result['blockers']))

    def test_budget_value_and_unknown_status_evidence_remain_separate(self):
        pages,q,a,_=inputs();last=json.loads(pages[1]);last['data']['task_list'][-1].update(budget=450,task_status='NEW_UNKNOWN')
        result=compare_supplied_task_pages([pages[0],json.dumps(last)],q,a,NOW)
        self.assertEqual(result['status'],'differs_requested_value');self.assertTrue(result['status_warnings'])
        self.assertIsNone(result['observed_task_status'])
        last['data']['task_list'][-1]['budget']='500.001'
        self.assertEqual(compare_supplied_task_pages([pages[0],json.dumps(last)],q,a,NOW)['status'],'inconclusive')

    def test_parent_account_scene_goal_and_scope_must_match_original_record(self):
        pages,q,a,_=inputs()
        for fields in ({'parent_plan_id':'9'},{'account_id':'456'},{'scene':'SMART_BOOST'},{'marketing_goal':'VIDEO_PROM_GOODS'}):
            with self.assertRaises(ValueError):compare_supplied_task_pages(pages,{**q,**fields},a,NOW)
        a['context']['scope']='plan'
        with self.assertRaises(ValueError):compare_supplied_task_pages(pages,q,a,NOW)

    def test_secret_duplicate_keys_retired_metrics_and_missing_data_are_handled_safely(self):
        pages,q,a,_=inputs();first=json.loads(pages[0]);first['data']['task_list'][0]['metrics']={'pay_convert_cnt_for_roi2_assist':99999}
        self.assertNotIn('99999',json.dumps(compare_supplied_task_pages([json.dumps(first),pages[1]],q,a,NOW)))
        for raw in ('{"code":0,"code":0}',json.dumps({'code':0,'token':'private-secret','data':{}}),'{"code":0,"data":{}}'):
            with self.assertRaises(ValueError) as caught:compare_supplied_task_pages([raw],q,a,NOW)
            self.assertNotIn('private-secret',str(caught.exception))

    def test_query_limits_times_and_documented_page_sizes_are_explicit(self):
        for kwargs in ({'page_size':11},{'max_pages':True},{'max_pages':21}):
            with self.assertRaises(ValueError):prepare_task_read_request('123',PARENT,TASK,'material_chase',START,END,CFG,**kwargs)
        with self.assertRaises(ValueError):prepare_task_read_request('123',PARENT,TASK,'one_click_control',START,END,CFG)
        pages,q,a,_=inputs();q['request_started_at']=(NOW-timedelta(minutes=6)).isoformat()
        with self.assertRaises(ValueError):compare_supplied_task_pages(pages,q,a,NOW)
        for size in (10,20,50,100):
            request=prepare_task_read_request('123',PARENT,TASK,'material_chase',START,END,CFG,page_size=size)
            self.assertEqual(request['params']['page_size'],size)
            self.assertNotIn('fields',request['params'])


class TaskReadClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'attempts.db';self.ledger=AttemptLedger(self.path)
        self.pages,self.query,self.attempt,self.request=inputs()
        self.ledger.claim('one','digest',NOW-timedelta(minutes=5),self.attempt['context']);self.ledger.finish('one','unknown',{})
        self.permit=ControlTaskReadPermit('123',self.request['digest'],NOW,NOW+timedelta(minutes=2),'mock-only-read-review')

    def invoke(self,client,permit=None,cfg=None,token=None,max_pages=2):
        return client.review_attempt(self.ledger,'one',cfg or CFG,permit or self.permit,token or (lambda:'test'),START,END,max_pages=max_pages,page_size=10)

    def client(self,transport,clock=None):return ControlTaskReadClient(enable_network=True,transport=transport,clock=clock or (lambda:NOW))

    def test_default_disabled_before_any_ledger_credentials_or_transport(self):
        ledger,token,transport=Mock(),Mock(),Mock()
        with self.assertRaises(PermissionError):ControlTaskReadClient(transport=transport).review_attempt(ledger,'one',CFG,None,token,START,END)
        ledger.connect.assert_not_called();token.assert_not_called();transport.assert_not_called()

    def test_all_pages_saved_idempotently_and_unknown_parent_hold_survives_restart(self):
        transport=Mock(side_effect=self.pages);first=self.invoke(self.client(transport))
        self.assertTrue(first['ok']);self.assertEqual(transport.call_count,2)
        self.assertEqual(first['comparison']['status'],'matches_requested_value')
        second=self.invoke(self.client(Mock(side_effect=self.pages)))
        self.assertEqual(first['comparison'],second['comparison'])
        restarted=AttemptLedger(self.path)
        with restarted.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM submission_attempts').fetchone()[0],'unknown')
            self.assertEqual(db.execute('SELECT count(*) FROM supplied_readback_reviews').fetchone()[0],1)
        with self.assertRaisesRegex(ValueError,'未明确'):
            restarted.claim('parent-change','new',NOW+timedelta(hours=2),{'account_id':'123','entity_id':PARENT,'scope':'plan'})

    def test_wrong_permit_family_digest_and_max_page_scope_do_not_read_credentials(self):
        token,transport=Mock(),Mock()
        wrong=PlanDetailPermit('123',self.request['digest'],NOW,NOW+timedelta(minutes=2),'mock')
        for permit,pages in ((wrong,2),(self.permit,1)):
            with self.assertRaises(PermissionError):self.invoke(self.client(transport),permit=permit,token=token,max_pages=pages)
        token.assert_not_called();transport.assert_not_called()

    def test_page_bound_is_honored_and_partial_evidence_cannot_confirm_value(self):
        request=prepare_task_read_request('123',PARENT,TASK,'material_chase',START,END,CFG,max_pages=1,page_size=10)
        permit=ControlTaskReadPermit('123',request['digest'],NOW,NOW+timedelta(minutes=2),'mock')
        transport=Mock(return_value=self.pages[0]);result=self.invoke(self.client(transport),permit=permit,max_pages=1)
        self.assertTrue(result['ok']);self.assertEqual(transport.call_count,1)
        self.assertEqual(result['comparison']['status'],'inconclusive');self.assertFalse(result['comparison']['pagination_exhausted'])

    def test_account_pause_during_first_page_stops_next_page_and_saves_nothing(self):
        cfg=copy.deepcopy(CFG)
        def transport(*args):cfg['accounts'][0]['observe']=False;return self.pages[0]
        mock=Mock(side_effect=transport);result=self.invoke(self.client(mock),cfg=cfg)
        self.assertFalse(result['ok']);self.assertEqual(mock.call_count,1)
        with self.ledger.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM supplied_readback_reviews').fetchone()[0],0)

    def test_expiry_before_next_page_and_second_page_failure_never_retry_or_leak(self):
        clock=Mock(side_effect=[NOW,NOW,NOW+timedelta(minutes=3)])
        transport=Mock(return_value=self.pages[0]);result=self.invoke(self.client(transport,clock))
        self.assertFalse(result['ok']);self.assertEqual(transport.call_count,1)
        transport=Mock(side_effect=[self.pages[0],TimeoutError('private-secret')])
        result=self.invoke(self.client(transport));self.assertFalse(result['automatic_retry']);self.assertEqual(transport.call_count,2)
        self.assertNotIn('private-secret',json.dumps(result));self.assertNotIn(b'private-secret',self.path.read_bytes())

    def test_mutating_transport_arguments_cannot_change_following_query_scope(self):
        params=[]
        def transport(query,page,token,timeout):
            params.append(dict(query));query['ad_id']='99';return self.pages[page-1]
        self.assertTrue(self.invoke(self.client(transport))['ok'])
        self.assertEqual([p['ad_id'] for p in params],[PARENT,PARENT])

    def test_falsey_transport_is_used_and_plan_records_rejected_before_credentials(self):
        pages=self.pages
        class Falsey:
            def __bool__(self):return False
            def __call__(self,params,page,*args):return pages[page-1]
        with patch('monitor.control_readback._get_task_page',side_effect=AssertionError('must not use default')):
            self.assertTrue(self.invoke(self.client(Falsey()))['ok'])
        with self.ledger.connect() as db:
            ctx=dict(self.attempt['context'],scope='plan');db.execute('UPDATE submission_attempts SET context_json=?',(json.dumps(ctx),))
        token=Mock()
        with self.assertRaises(ValueError):self.invoke(self.client(Mock()),token=token)
        token.assert_not_called()

    def test_fixed_get_query_no_status_filter_no_redirect_and_raw_size_bound(self):
        returned=Mock();returned.__enter__=Mock(return_value=returned);returned.__exit__=Mock(return_value=False)
        returned.read.return_value=self.pages[0].encode()
        returned.geturl.side_effect=lambda:opener.open.call_args.args[0].full_url
        opener=Mock();opener.open.return_value=returned
        with patch('monitor.control_readback.urllib.request.build_opener',return_value=opener) as build:
            _get_task_page(self.request['params'],1,'test',10)
            request=opener.open.call_args.args[0]
            self.assertEqual(request.method,'GET');self.assertIsNone(request.data)
            self.assertIn('/control_task/list/?',request.full_url);self.assertIn('search_keyword',request.full_url)
            self.assertNotIn('task_status',request.full_url);self.assertNotIn('fields=',request.full_url)
            self.assertIsNone(build.call_args.args[1].redirect_request(None,None,None,None,None,None))
            returned.read.return_value=b' '*1048577
            with self.assertRaises(ValueError):_get_task_page(self.request['params'],1,'test',10)

    def test_shared_write_transport_does_not_erase_duplicate_response_keys_before_decoding(self):
        returned=Mock();returned.__enter__=Mock(return_value=returned);returned.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=returned
        for raw in (b'{"code":0,"data":{"task_ids":[1],"task_ids":[]}}',b'{"code":0,"data":{"x":NaN}}'):
            returned.read.return_value=raw
            with patch('monitor.submission.urllib.request.build_opener',return_value=opener):
                with self.assertRaises(ValueError):_post_control_status('{}','test',10)


if __name__=='__main__':unittest.main()
