import copy
import tempfile
import threading
import unittest
from pathlib import Path
from monitor.official_api import OfficialAPI, CredentialVault, ACCOUNT_FIELDS
from monitor.official_read_models import monitoring_model
from monitor.schema import now_cn


class OfficialMonitoringTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.calls=[]
        self.account={'id':'1999999999999002','name':'甲户','scene':'product','delivery_entry':'both'}
        self.ids={'UNI_PROJECT':'89581211092803411','OVERALL_PROJECT':'89041279016210106'}
        self.vault=CredentialVault(self.root/'test-vault',protect=lambda x:b'test:'+x,unprotect=lambda x:x[5:])
        self.vault.save({'version':1,'app':{},'pending':{},'grants':{'g':{'access_token':'not-live',
            'refresh_token':'not-live-refresh','expires_at':20000,'refresh_expires_at':30000,'label':'测试','accounts':[]}}})
        self.api=OfficialAPI(self.root,vault=self.vault,clock=lambda:10000,transport=self.transport)

    def tearDown(self):self.tmp.cleanup()

    def plan(self,family,bid):
        return {'ad_info':{'id':int(self.ids[family]),'adlab_scene':family,
            'marketing_goal':'LIVE_PROM_GOODS' if self.account['scene']=='live' else 'VIDEO_PROM_GOODS',
            'smart_bid_type':bid,'name':'计划<测试>','budget':300,'roi2_goal':3.5,
            'deep_external_action':'AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI','opt_status':'ENABLE'},
            'room_info':[{'anchor_id':'71110310948','anchor_name':'直播间'}] if self.account['scene']=='live' else []}

    def transport(self,name,query,token=None):
        self.calls.append((name,query))
        if name=='plans':
            rows=[] if query['filtering']['smart_bid_type']=='SMART_BID_CONSERVATIVE' else [self.plan(query['adlab_scene'],query['filtering']['smart_bid_type'])]
            return {'ad_list':rows,'page_info':{'total_num':len(rows)}}
        if name=='report_config':
            topic=query['data_topics'][0];key='anchor_show_id' if 'LIVE_AWEME' in topic else 'ad_id'
            return {'custom_config_datas':[{'data_topic':topic,'dimensions':[{'field':key}],
                'metrics':[{'field':k} for k in ACCOUNT_FIELDS]}]}
        if name=='report_data':
            overall=query['data_topic'].startswith('OVERALL_');key=query['dimensions'][0]
            identity='71110310948' if key=='anchor_show_id' else self.ids['OVERALL_PROJECT' if overall else 'UNI_PROJECT']
            return {'rows':[{'dimensions':{key:{'Value':int(identity)}},'metrics':{
                'stat_cost_for_roi2':{'Value':60 if overall else 20},
                'total_prepay_and_pay_order_roi2':{'Value':2 if overall else 4}}}],
                'page_info':{'total_number':1}}
        if name=='author_report':
            return {'list':[{'advertiser_id':int(self.account['id']),'aweme_id':int(query['aweme_id']),
                'stat_cost':100,'total_prepay_and_pay_order_roi2':3}], 'page_info':{'total_number':1}}
        raise AssertionError(name)

    def test_product_entries_keep_exact_ids_and_attach_only_matching_plan_reports(self):
        result=self.api.read_plans(self.account,'g')['result'];rows=result['plans']
        self.assertEqual(len(rows),2);self.assertEqual([p['plan_id'] for p in rows],list(self.ids.values()))
        self.assertEqual([p['metrics']['spend'] for p in rows],[20,60])
        self.assertEqual([p['metrics']['roi'] for p in rows],[4,2])
        self.assertEqual(rows[0]['target_roi_basis'],'net');self.assertEqual(rows[0]['metrics_source']['kind'],'plan_report')
        self.assertTrue(result['complete']);self.assertEqual(result['ad_changes'],0)
        self.assertTrue(all('not-live' not in str(p) for p in self.api.status(8877)['plan_readings'].values()))
        self.assertEqual(len([c for c in self.calls if c[0]=='plans']),4)
        self.assertTrue(all(q['filtering']['status']=='ALL_INCLUDE_DELETED' for name,q in self.calls if name=='plans'))

    def test_universal_live_reports_join_exact_aweme_id_and_bid_type(self):
        self.account.update(scene='live',delivery_entry='uni_promotion')
        result=self.api.read_plans(self.account,'g')['result'];p=result['plans'][0]
        self.assertEqual(p['metrics']['spend'],100)
        q=next(q for name,q in self.calls if name=='author_report')
        self.assertEqual(q['aweme_id'],71110310948)
        self.assertEqual(q['filtering'],{'order_platform':'QIANCHUAN','smart_bid_type':'SMART_BID_CUSTOM'})
        self.assertTrue(all('status' not in q['filtering'] for name,q in self.calls if name=='plans'))

    def test_shared_anchor_does_not_assign_unfiltered_report_to_universal_plan(self):
        self.account['scene']='live'
        rows=self.api.read_plans(self.account,'g')['result']['plans']
        self.assertEqual(rows[0]['metric_status'],'ambiguous_anchor');self.assertIsNone(rows[0]['metrics']['spend'])
        self.assertEqual(rows[1]['metrics']['spend'],60)
        self.assertFalse(any(name=='author_report' for name,_ in self.calls))

    def test_foreign_or_wrong_family_report_aborts_without_replacing_previous_data(self):
        baseline=self.api.read_plans(self.account,'g')['result'];old=self.transport
        def wrong(name,q,token=None):
            if name=='plan_detail':return {'ad_id':99998,'marketing_goal':'VIDEO_PROM_GOODS'}
            result=old(name,q,token)
            if name=='report_data':result['rows'][0]['dimensions']['ad_id']['Value']=99999
            return result
        self.api.transport=wrong
        with self.assertRaises(ValueError):self.api.read_plans(self.account,'g')
        self.assertEqual(self.api.plan_readings[self.account['id']],baseline)

    def test_report_discovered_managed_plan_requires_exact_scoped_detail(self):
        old=self.transport;managed='89521066345122920'
        def additional(name,q,token=None):
            if name=='plan_detail':
                self.assertEqual(q,{'advertiser_id':int(self.account['id']),'ad_id':int(managed)})
                return {'ad_id':int(managed),'marketing_goal':'VIDEO_PROM_GOODS','adlab_scene':1,
                    'uni_prom_shop_id':123,'name':'全店托管','opt_status':'ENABLE','room_info':[],
                    'delivery_setting':{'budget':8000,'roi2_goal':3.5,'smart_bid_type':'SMART_BID_CUSTOM'}}
            result=old(name,q,token)
            if name=='report_data' and q['data_topic'].startswith('OVERALL_'):
                result['rows'].append({'dimensions':{'ad_id':{'Value':int(managed)}},
                    'metrics':{'stat_cost_for_roi2':{'Value':50},'total_prepay_and_pay_order_roi2':{'Value':3}}})
                result['page_info']['total_number']=2
            return result
        self.api.transport=additional
        result=self.api.read_plans(self.account,'g')['result'];p=result['plans'][-1]
        self.assertEqual(p['plan_id'],managed);self.assertEqual(p['metrics']['spend'],50)
        self.assertEqual(p['configuration_source'],'report_and_detail');self.assertTrue(p['full_shop_managed'])
        self.assertEqual(p['entry_identity_source'],'report_topic');self.assertEqual(p['native_adlab_scene'],1)
        self.assertEqual(result['entries'][-1]['report_discovered_plans'],1)

    def test_report_gaps_are_not_zero_and_unknown_goal_basis_is_not_guessed(self):
        old=self.transport
        def empty(name,q,token=None):
            result=old(name,q,token)
            if name=='report_data':return {'rows':[],'page_info':{'total_number':0}}
            if name=='plans':
                for p in result['ad_list']:p['ad_info']['deep_external_action']=None
            return result
        self.api.transport=empty
        rows=self.api.read_plans(self.account,'g')['result']['plans']
        self.assertTrue(all(p['metric_status']=='not_reported' and p['metrics']['spend'] is None for p in rows))
        self.assertTrue(all(p['target_roi_basis']=='unknown' for p in rows))

    def test_list_filter_mismatch_and_rounded_id_are_rejected(self):
        old=self.transport
        for change in ({'adlab_scene':'OTHER'},{'id':89581211092803411.0}):
            def wrong(name,q,token=None):
                result=old(name,q,token)
                if name=='plans':
                    for p in result['ad_list']:p['ad_info'].update(change)
                return result
            self.api.transport=wrong
            with self.subTest(change=change),self.assertRaises(ValueError):self.api.read_plans(self.account,'g')
        self.assertEqual(self.api.plan_readings,{})

    def test_read_model_keeps_api_reading_separate_from_execution_and_selected_entry(self):
        result=self.api.read_plans(self.account,'g')['result']
        selected={**self.account,'delivery_entry':'chengfang'}
        model=monitoring_model(selected,result,{},now_cn())
        self.assertEqual(model['backend'],'official_api');self.assertEqual(len(model['plans']),1)
        self.assertFalse(model['plans'][0]['execution_ready']);self.assertEqual(model['execution_plan_count'],0)
        self.assertIsNone(model['plans'][0]['data_at']);self.assertEqual(model['plans'][0]['metrics']['budget'],300)
        self.assertEqual(model['plans'][0]['configuration']['roi_basis'],'net')

    def test_slow_read_does_not_block_connection_status(self):
        entered=threading.Event();release=threading.Event();completed=threading.Event();old=self.transport
        def slow(name,q,token=None):
            if name=='plans' and not entered.is_set():entered.set();release.wait(3)
            return old(name,q,token)
        self.api.transport=slow
        reader=threading.Thread(target=lambda:self.api.read_plans(self.account,'g'));reader.start()
        self.assertTrue(entered.wait(1))
        status=threading.Thread(target=lambda:(self.api.status(8877),completed.set()));status.start()
        try:self.assertTrue(completed.wait(1),'Status must stay responsive while report IO is pending')
        finally:release.set();reader.join(4);status.join(4)

    def test_deleted_plan_is_never_displayed_as_running(self):
        result=self.api.read_plans(self.account,'g')['result'];result['plans'][0]['status']='DELETE'
        model=monitoring_model(self.account,result,{},now_cn())
        self.assertFalse(model['plans'][0]['running']);self.assertEqual(model['plans'][0]['plan_status'],'DELETE')


if __name__=='__main__':unittest.main()
