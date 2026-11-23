import copy
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from monitor.official_api import OfficialAPI,CredentialVault,OfficialBusinessError,OfficialWriteTransport
from monitor.official_actions import OfficialActions
from monitor.official_workflow import scan,digest
from monitor.cruise_rules import default_profile
from monitor.schema import now_cn


class WorkflowTests(unittest.TestCase):
    def test_cancelling_unsent_draft_or_preflight_does_not_wait_for_another_claim(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        for state in ('draft','checking'):
            self.api.scan_workflow(self.account,self.profile,self.pid,materials=False)
            r=self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':'task_budget','task_id':'10001','value':'150'})
            with self.queue.db() as db:db.execute('UPDATE api_actions SET state=? WHERE id=?',(state,r['id']))
            entered=threading.Event();release=threading.Event()
            def hold():
                with self.queue.gate:entered.set();release.wait(4)
            holder=threading.Thread(target=hold);holder.start();self.assertTrue(entered.wait(1))
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:result=pool.submit(self.queue.cancel,{'id':r['id']}).result(timeout=1)
                self.assertEqual(result['ad_changes'],0);self.assertTrue(holder.is_alive())
            finally:release.set();holder.join()
            with self.assertRaises(ValueError):self.confirm(r)
        self.assertEqual(self.posts,[])

    def test_cancellation_never_changes_a_potentially_submitted_intent(self):
        self.api.scan_workflow(self.account,self.profile,self.pid,materials=False)
        r=self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':'task_budget','task_id':'10001','value':'150'})
        for state in ('submitting','unknown','submitted_pending_verification'):
            with self.queue.db() as db:db.execute('UPDATE api_actions SET state=? WHERE id=?',(state,r['id']))
            with self.assertRaises(ValueError):self.queue.cancel({'id':r['id']})
            with self.queue.db() as db:self.assertEqual(db.execute('SELECT state FROM api_actions WHERE id=?',(r['id'],)).fetchone()[0],state)
        self.assertEqual(self.posts,[])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.calls=[];self.posts=[];self.created=None
        self.account={'id':'1999999999999002','name':'测试户','scene':'live','delivery_entry':'both','observe':True}
        self.pid='8218616704149013';self.uid='79987870170';self.anchor='8096049915736341'
        vault=CredentialVault(self.root/'vault',protect=lambda v:v,unprotect=lambda v:v)
        vault.save({'version':1,'app':{},'pending':{},'grants':{'g':{'access_token':'paper-token','refresh_token':'paper-refresh',
            'expires_at':time.time()+9000,'refresh_expires_at':time.time()+99999,'label':'测试','accounts':[]}}})
        self.api=OfficialAPI(self.root,vault=vault,transport=self.transport)
        self.plan={'plan_id':self.pid,'delivery_entry':'chengfang','name':'测试母计划','anchors':[{'id':self.uid}],
            'captured_at':now_cn().isoformat(),'budget':10000,'opt_status':'ENABLE','status':'DELIVERY_OK',
            'smart_bid_type':'SMART_BID_CUSTOM','deep_external_action':'AD_CONVERT_TYPE_LIVE_PAY_ROI','configuration_source':'plan_list'}
        self.api.plan_readings[self.account['id']]={'date':now_cn().date().isoformat(),'plans':[self.plan]}
        self.api.read_plans=lambda *args,**kwargs:{'ok':True,'result':copy.deepcopy(self.api.plan_readings[self.account['id']])}
        self.profile=default_profile();self.profile.update(enabled=True,operation_start='00:00',operation_end='23:59')
        self.profile['features']['f5'].update(enabled=True,batch_size=2,rules=[{'chase_type':'控成本追投','optimization_target':'直播间成交',
            'daily_budget':200,'bid_value':20.88,'material_spend_min':100,'material_spend_max':None,'material_roi_min':3}])
        self.queue=OfficialActions(self.root/'actions.db',self.api,lambda:[self.account],lambda aid:(self.profile,'r1'),writer=self.transport)
        self.materials=[('89581211092803411',500,4),('89041279016210106',400,4.2),('84300024958326955',300,4.5)]
        self.bad_anchor=False;self.fail_post=False;self.bad_readback=False;self.degrade=False;self.paused_task=False;self.paused_plan=False

    def tearDown(self):self.tmp.cleanup()

    def transport(self,name,q,token=None):
        self.calls.append((name,copy.deepcopy(q)))
        if name=='plan_detail':return {'ad_id':int(self.pid),'marketing_goal':'LIVE_PROM_GOODS','opt_status':'DISABLE' if self.paused_plan else 'ENABLE','adlab_scene':'OVERALL_PROJECT'}
        if name=='control_tasks':
            if q.get('filtering',{}).get('search_keyword'):
                if q['filtering']['search_keyword']=='10001':return {'task_list':[{'id':10001,'task_status':'DISABLE' if self.paused_task else 'PROCESSING',
                    'budget':100,'material_list':[{'material_id':int(self.materials[0][0])}],
                    'metrics':{'stat_cost_for_roi2_assist':50,'total_prepay_and_pay_order_roi2_assist':4}}],'page_info':{'total_number':1}}
                t=copy.deepcopy(self.created)
                if self.bad_readback:t['budget']=300
                return {'task_list':[t],'page_info':{'total_number':1}}
            rows=[] if q['scene']=='SMART_BOOST' else [{'id':10001,'task_status':'PROCESSING','budget':100,
                'material_list':[{'material_id':int(self.materials[0][0])}],'metrics':{'stat_cost_for_roi2_assist':50,'total_prepay_and_pay_order_roi2_assist':4}}]
            return {'task_list':rows,'page_info':{'total_number':len(rows)}}
        if name=='report_config':
            return {'custom_config_datas':[{'data_topic':'OVERALL_ROI_LIVE_MATERIAL_VIDEO',
                'dimensions':[{'field':k} for k in ['material_id','anchor_id','roi2_material_video_name']]+[{'field':'roi2_material_type_v3','filter_config':{'operator':7,'filter_required':True,'range_values':[{'label':'3','value':'视频'}]}}],
                'metrics':[{'field':k} for k in ['stat_cost_for_roi2','total_prepay_and_pay_order_roi2']]}]}
        if name=='report_data':
            if q['data_topic']=='OVERALL_ROI_LIVE_AWEME':return {'rows':[{'dimensions':{'anchor_show_id':{'Value':self.uid},'anchor_id':{'Value':self.anchor}}}],'page_info':{'total_number':1}}
            ids=next((f['values'] for f in q['filters'] if f['field']=='material_id'),None)
            rows=[{'dimensions':{'material_id':{'Value':mid},'anchor_id':{'Value':'444' if self.bad_anchor else self.anchor},'roi2_material_video_name':{'Value':'素材<'+mid+'>'}},
                'metrics':{'stat_cost_for_roi2':{'Value':cost},'total_prepay_and_pay_order_roi2':{'Value':1 if self.degrade else roi}}} for mid,cost,roi in self.materials if ids is None or mid in ids]
            return {'rows':rows,'page_info':{'total_number':len(rows)}}
        if name=='plan_materials':
            mid=q['filtering']['search_keyword']
            return {'ad_material_infos':[{'material_info':{'video_material':{'material_id':int(mid)}},'audit_status':'PASS','material_status':'DELIVERY_OK','stats_info':{'stat_cost_for_roi2':0}}],
                'page_info':{'total_number':1}}
        if name=='create_control_task':
            self.posts.append(copy.deepcopy(q))
            if self.fail_post:raise OSError('network uncertain')
            self.created={'id':10000001,'name':q['name'],'budget':q['budget'],'bid':q['bid'],'smart_bid_type':q['smart_bid_type'],
                'external_action_name':'直播间支付','deep_external_action_name':None,
                'material_list':[{'material_id':mid} for mid in q['material_ids']]}
            return {'id':10000001}
        if name=='pause_control_task':
            self.posts.append(copy.deepcopy(q));self.paused_task=True;return {'task_ids':[10001],'errors':[]}
        if name=='pause_plan':
            self.posts.append(copy.deepcopy(q));self.paused_plan=True;return {'results':[{'ad_id':int(self.pid),'flag':True}]}
        raise AssertionError(name)

    def scanned(self):return scan(self.api,self.account,self.profile,self.pid,materials=True)
    def prepared(self):
        value=self.scanned()
        return self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':'material_chase','batch_key':value['batches'][0]['key']})
    def confirm(self,r):return self.queue.confirm({'id':r['id'],'intent_digest':r['intent_digest'],'confirmed':True})

    def test_scan_uses_report_effect_not_zero_inventory_and_excludes_running(self):
        value=self.scanned();batch=value['batches'][0]
        self.assertEqual(batch['material_ids'],[v[0] for v in self.materials[1:]])
        self.assertEqual(batch['materials'][0]['metrics']['spend'],400)
        self.assertTrue(batch['ready']);self.assertEqual(self.posts,[])
        self.assertTrue(all(q['filtering']['task_status']=='PROCESSING' for name,q in self.calls if name=='control_tasks'))
        self.assertTrue(value['tasks_complete']);self.assertEqual(value['date_end'],now_cn().date().isoformat())

    def test_native_full_created_timestamp_normalizes_beijing_time_but_date_only_stays_unknown(self):
        from monitor.official_workflow import task_created_at
        self.assertEqual(task_created_at('2026-10-27 23:15:00'),'2026-10-27T23:15:00+08:00')
        self.assertEqual(task_created_at('2026-10-27T15:15:00Z'),'2026-10-27T23:15:00+08:00')
        self.assertIsNone(task_created_at('2026-10-27'));self.assertIsNone(task_created_at('bad'))

    def test_repeat_option_is_account_setting(self):
        self.profile['features']['f5']['allow_running_materials']=True
        self.assertEqual(self.scanned()['batches'][0]['material_ids'],[m[0] for m in self.materials[:2]])

    def test_full_sweep_keeps_paging_after_first_batch_until_spend_floor(self):
        self.profile['features']['f5'].update(batch_size=5,allow_running_materials=True)
        self.profile['features']['f5']['rules'][0]['material_spend_min']=200
        self.materials=[(str(89581211092803411+i),330-i,4) for i in range(230)]
        real=self.api.transport
        def paged(name,q,token=None):
            value=real(name,q,token)
            if name=='report_data' and q['data_topic']=='OVERALL_ROI_LIVE_MATERIAL_VIDEO' and not any(f['field']=='material_id' for f in q['filters']):
                value['rows']=value['rows'][(q['page']-1)*100:q['page']*100]
            return value
        self.api.transport=paged
        v=self.scanned();self.assertEqual(v['pages_read'],2);self.assertEqual(v['stop_reason'],'spend_threshold')
        self.assertTrue(v['eligible_range_complete']);self.assertFalse(v['materials_complete'])
        self.assertEqual(len(v['batches']),26);self.assertEqual(sum(len(b['material_ids']) for b in v['batches']),130)
        self.assertEqual(v['unbatched_materials'],[]);self.assertEqual(self.posts,[])
        self.assertNotIn('凑齐一批后停止',v['notes'][0])

    def test_overlapping_rules_do_not_repeat_ids_and_short_tail_keeps_exact_batch_budget(self):
        self.profile['features']['f5'].update(batch_size=2,allow_running_materials=True)
        self.profile['features']['f5']['rules']*=2
        v=self.scanned();self.assertEqual(len(v['batches']),1)
        self.assertEqual(v['batches'][0]['material_ids'],[m[0] for m in self.materials[:2]])
        self.assertEqual(len(v['unbatched_materials']),1);self.assertEqual(v['batches'][0]['native_contract']['budget'],'200')

    def test_native_boost_material_list_can_be_null(self):
        real=self.api.transport
        def wrapped(name,q,token=None):
            if name=='control_tasks' and q['scene']=='SMART_BOOST':return {'task_list':[{'id':999,'task_status':'PROCESSING','material_list':None,'metrics':{'stat_cost_for_roi2_assist':0}}],'page_info':{'total_number':1}}
            return real(name,q,token)
        self.api.transport=wrapped
        self.assertEqual(self.scanned()['tasks'][0]['material_ids'],[])

    def test_task_only_read_does_not_retimestamp_or_erase_material_batches(self):
        first=self.scanned();old=first['materials_captured_at']
        later=scan(self.api,self.account,self.profile,self.pid,materials=False)
        self.assertEqual(later['batches'],first['batches']);self.assertEqual(later['materials_captured_at'],old)

    def test_foreign_anchor_does_not_publish_partial_cache(self):
        first=self.scanned();self.bad_anchor=True
        with self.assertRaisesRegex(ValueError,'其他抖音号'):self.scanned()
        self.assertEqual(self.api.workflows[self.account['id']+':'+self.pid],first)

    def test_unknown_mode_stays_blocked_without_switching_goal(self):
        self.profile['features']['f5']['rules'][0]['optimization_target']='综合ROI'
        self.assertFalse(self.scanned()['batches'][0]['ready'])

    def test_negative_native_placeholder_is_counted_but_never_chased(self):
        self.materials.append(('-2',100,4))
        value=self.scanned()
        self.assertEqual(value['scanned_rows'],4)
        self.assertEqual(len(value['unresolved_material_rows']),1)
        self.assertFalse(value['material_identities_complete'])
        self.assertNotIn('-2',value['batches'][0]['material_ids'])

    def test_prepare_does_not_post_and_duplicate_draft_is_blocked(self):
        r=self.prepared();self.assertEqual(self.posts,[]);self.assertEqual(r['state'],'draft')
        self.assertEqual(r['intent']['body']['material_ids'],[int(m[0]) for m in self.materials[1:]])
        self.assertNotIn('duration',r['intent']['body']);self.assertEqual(r['intent']['body']['budget'],200)
        with self.assertRaisesRegex(ValueError,'已有待确认'):self.prepared()

    def test_confirm_one_post_and_independent_exact_readback(self):
        r=self.prepared();out=self.confirm(r)
        self.assertEqual(out['state'],'verified');self.assertEqual(len(self.posts),1)
        self.assertEqual(out['result']['task_id'],'10000001')
        self.assertTrue(any(n=='report_data' and any(f['field']=='material_id' for f in q['filters']) for n,q in self.calls))
        with self.assertRaisesRegex(ValueError,'不重复执行'):self.confirm(r)
        self.assertEqual(len(self.posts),1)

    def test_checking_is_published_before_network_and_blocks_other_writers(self):
        r=self.prepared();real=self.queue._fresh
        def observe(*args):
            value=self.queue.status()[0]
            self.assertEqual(value['state'],'checking');self.assertTrue(self.queue.has_pending())
            self.assertEqual(value['result']['phase'],'preflight_running')
            self.assertEqual(self.posts,[])
            return real(*args)
        self.queue._fresh=observe
        self.assertEqual(self.confirm(r)['state'],'verified');self.assertEqual(len(self.posts),1)

    def test_changed_effect_or_profile_stops_before_post(self):
        r=self.prepared();self.degrade=True
        with self.assertRaisesRegex(ValueError,'效果已不符合'):self.confirm(r)
        self.assertEqual(self.posts,[])
        self.degrade=False;self.profile['features']['f5']['batch_size']=3
        with self.assertRaisesRegex(ValueError,'规则已变化'):self.confirm(r)

    def test_material_preflight_failure_records_exact_id_without_post(self):
        r=self.prepared();real=self.api.transport;bad=r['intent']['material_ids'][0]
        def changed(name,q,token=None):
            value=real(name,q,token)
            if name=='plan_materials' and q['filtering']['search_keyword']==bad:
                value['ad_material_infos'][0]['material_status']='DISABLE'
            return value
        self.api.transport=changed
        with self.assertRaisesRegex(ValueError,bad+'.*DISABLE'):self.confirm(r)
        saved=self.queue.status()[0]
        self.assertEqual(saved['state'],'draft');self.assertEqual(saved['result']['phase'],'preflight')
        self.assertEqual(saved['result']['ad_changes'],0);self.assertIn(bad,saved['result']['message'])
        self.assertEqual(self.posts,[])

    def test_uncertain_post_is_held_and_never_retried(self):
        r=self.prepared();self.fail_post=True
        self.assertEqual(self.confirm(r)['state'],'unknown')
        with self.assertRaises(ValueError):self.confirm(r)
        with self.assertRaisesRegex(ValueError,'已有待确认'):self.prepared()
        self.assertEqual(len(self.posts),1)

    def test_mismatched_readback_is_not_verified(self):
        r=self.prepared();self.bad_readback=True
        out=self.confirm(r);self.assertEqual(out['state'],'submitted_pending_verification')
        self.assertEqual(out['result']['reply']['id'],10000001)
        self.bad_readback=False
        again=self.queue.recheck({'id':r['id']})
        self.assertEqual(again['state'],'verified');self.assertEqual(len(self.posts),1)

    def test_pause_task_and_plan_require_their_own_confirmation(self):
        self.scanned()
        r=self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'task_id':'10001','action':'pause_task'})
        self.assertFalse(self.paused_task);self.assertEqual(self.confirm(r)['state'],'verified')
        self.assertEqual(self.posts[-1]['opt_type'],'PAUSE');self.assertEqual(self.posts[-1]['task_ids'],[10001])
        r=self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':'pause_plan'})
        self.assertFalse(self.paused_plan);self.assertEqual(self.confirm(r)['state'],'verified')
        self.assertEqual(self.posts[-1]['ad_ids'],[int(self.pid)])

    def test_native_business_post_is_never_retried(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return b'{"code":50000,"message":"system error","data":{}}'
        class Opener:
            def __init__(self):self.calls=0
            def open(self,*args,**kwargs):self.calls+=1;return Response()
        opener=Opener()
        with patch('monitor.official_api.urllib.request.build_opener',return_value=opener):
            with self.assertRaises(OfficialBusinessError):OfficialWriteTransport(sleeper=lambda _:None)('create_control_task',{'advertiser_id':1},'paper-token')
        self.assertEqual(opener.calls,1)

    def test_other_account_plan_and_digest_are_rejected(self):
        with self.assertRaises(ValueError):self.queue.prepare({'account_id':'9999','plan_id':self.pid,'action':'pause_plan'})
        r=self.prepared()
        with self.assertRaises(ValueError):self.queue.confirm({'id':r['id'],'intent_digest':'altered','confirmed':True})
        self.assertEqual(self.posts,[])

    def test_cancellation_never_submits(self):
        r=self.prepared();self.queue.cancel({'id':r['id']})
        with self.assertRaises(ValueError):self.confirm(r)
        self.assertEqual(self.posts,[])


if __name__=='__main__':unittest.main()
