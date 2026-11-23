import copy
import json
import unittest
from datetime import timedelta

from monitor.ai import make_package
from monitor.evaluation import evaluate_all
from monitor.official_reports import convert_official_reports,official_evidence
from monitor.official_fields import FORMAT,ENDPOINT,FIELDS
from monitor.reporting import build_report,report_markdown
from test_engine import NOW,settings


def payload(account='123'):
    return {'format':FORMAT,'reports':[{'endpoint':ENDPOINT,'captured_at':NOW.isoformat(),'data_at':None,
        'query':{'advertiser_id':account,'start_time':NOW.replace(hour=0,minute=0).isoformat(),'end_time':NOW.isoformat(),
            'adlab_scene':'OVERALL_PROJECT','data_period':'ALL_DATA','marketing_goal':'LIVE_PROM_GOODS','order_platform':'QIANCHUAN',
            'fields':list(FIELDS)},
        'response':{'code':0,'message':'not persisted','request_id':'PRIVATE_REQUEST_ID',
            'data':{'stat_cost_for_roi2':100,'total_prepay_and_pay_order_roi2':4.25,'total_pay_order_gmv_for_roi2':320,
                    'total_pay_order_gmv_include_coupon_for_roi2':400,'total_pay_order_count_for_roi2':10,
                    'total_prepay_order_gmv_for_roi2':25,'total_pay_order_coupon_amount_for_roi2':60,
                    'total_prepay_and_pay_settle_roi2_1h':3.1,'total_order_settle_amount_for_roi2_1h':310,
                    'total_prepay_and_pay_settle_overall_roi2_1h':2.9,'total_refund_order_gmv_for_roi2_1h_rate':.05}}}]}


def convert(value=None,cfg=None):return convert_official_reports(value or payload(),cfg or settings(),NOW)


class OfficialReportTests(unittest.TestCase):
    def test_reported_point_values_remain_separate_from_canonical_numerators(self):
        out=convert();s=out['snapshots'][0];m=s['metrics']
        self.assertEqual(m['spend'],100);self.assertEqual(m['roi'],4.25)
        self.assertEqual(m['official_actual_paid'],320);self.assertEqual(m['official_total_deal_amount'],400)
        self.assertEqual(m['official_presale_amount'],25);self.assertEqual(m['official_coupon_amount'],60)
        self.assertNotIn('gmv',m);self.assertNotIn('base_gmv',m);self.assertNotIn('net_gmv',m)
        self.assertEqual(m['net_roi'],3.1);self.assertNotIn('marketing_roi',m)
        self.assertEqual(m['official_comprehensive_roi'],2.9)
        self.assertNotIn('orders',m);self.assertEqual(m['official_deal_orders'],10)

    def test_capture_or_query_end_never_replaces_missing_data_time(self):
        out=convert();s=out['snapshots'][0]
        self.assertIsNone(s['source']['data_at']);self.assertEqual(s['source']['metric_period'],'day')
        self.assertTrue(any('缺少页面数据更新时间' in v for v in s['quality']['issues']))
        self.assertEqual(out['preview'][0]['data_at'],None)

    def test_explicit_data_time_must_be_inside_query_and_not_after_capture(self):
        p=payload();frame=p['reports'][0];frame['data_at']=NOW.isoformat()
        self.assertEqual(convert(p)['snapshots'][0]['source']['data_at'],NOW.isoformat())
        for value in ((NOW+timedelta(hours=1)).isoformat(),(NOW-timedelta(days=1)).isoformat(),''):
            p=payload();p['reports'][0]['data_at']=value
            with self.assertRaises(ValueError):convert(p)

    def test_partial_or_multiday_range_cannot_become_daily_cumulative(self):
        for change in ({'start_time':NOW.replace(hour=10).isoformat()}, {'start_time':(NOW-timedelta(days=1)).replace(hour=0).isoformat()}):
            p=payload();p['reports'][0]['query'].update(change)
            s=convert(p)['snapshots'][0]
            self.assertEqual(s['source']['metric_period'],'unknown');self.assertIsNotNone(s['source']['period_start'])

    def test_future_daily_query_end_is_not_a_data_update_claim(self):
        p=payload();p['reports'][0]['query']['end_time']=NOW.replace(hour=23,minute=59,second=59).isoformat()
        s=convert(p)['snapshots'][0];self.assertIsNone(s['source']['data_at']);self.assertEqual(s['source']['metric_period'],'day')

    def test_unknown_or_foreign_account_and_mismatching_response_id_are_rejected(self):
        for account in ('456',True,123.0,'00123'):
            p=payload(account)
            with self.assertRaises(ValueError):convert(p)
        p=payload();p['reports'][0]['response']['data']['advertiser_id']='456'
        with self.assertRaises(ValueError):convert(p)
        p=payload(123);p['reports'][0]['response']['data']['advertiser_id']=123
        self.assertEqual(convert(p)['snapshots'][0]['account_id'],'123')

    def test_marketing_goal_must_match_account_scene_or_be_explicit_all(self):
        p=payload();p['reports'][0]['query']['marketing_goal']='VIDEO_PROM_GOODS'
        with self.assertRaises(ValueError):convert(p)
        cfg=settings();cfg['accounts'][0]['scene']='product'
        s=convert(p,cfg)['snapshots'][0];self.assertEqual(s['scene'],'product')
        p['reports'][0]['query']['marketing_goal']='ALL';self.assertTrue(convert(p,cfg)['snapshots'])

    def test_error_empty_list_and_token_responses_cannot_be_imported(self):
        for response in ({'code':1,'message':'PRIVATE_FAILURE','data':{}},{'code':False,'data':{}},{'data':{}},
                         {'code':0,'data':[]},{'code':0,'data':{}},{'code':0,'data':{'access_token':'PRIVATE_TOKEN'}}):
            p=payload();p['reports'][0]['response']=response
            with self.assertRaises(ValueError) as caught:convert(p)
            self.assertNotIn('PRIVATE',str(caught.exception))

    def test_credentials_are_rejected_at_any_depth_without_echo(self):
        for field in ('Access-Token','refresh_token','Authorization','Cookie','headers','app_secret','auth_code','localStorage','api_key'):
            p=payload();p['reports'][0]['response']['data']['nested']={field:'PRIVATE_CREDENTIAL'}
            with self.assertRaises(ValueError) as caught:convert(p)
            self.assertNotIn('PRIVATE_CREDENTIAL',str(caught.exception))

    def test_only_exact_supported_endpoint_and_format_are_accepted(self):
        for endpoint in ('/open_api/oauth2/access_token/','https://api.oceanengine.com'+ENDPOINT,ENDPOINT+'?token=x',ENDPOINT.replace('all_promotion','uni_promotion')):
            p=payload();p['reports'][0]['endpoint']=endpoint
            with self.assertRaises(ValueError):convert(p)
        p=payload();p['format']='any-other-report'
        with self.assertRaises(ValueError):convert(p)

    def test_query_requires_exact_range_type_period_platform_and_requested_fields(self):
        for field in ('start_time','end_time','adlab_scene','data_period','order_platform','marketing_goal','fields'):
            p=payload();del p['reports'][0]['query'][field]
            with self.assertRaises(ValueError):convert(p)
        p=payload();p['reports'][0]['query']['unsupported']=1
        with self.assertRaises(ValueError):convert(p)

    def test_wrong_ranges_and_future_capture_are_rejected(self):
        for mutate in (lambda f:f['query'].update(start_time=(NOW+timedelta(hours=1)).isoformat()),
                       lambda f:f['query'].update(end_time=(NOW-timedelta(days=1)).isoformat()),
                       lambda f:f.update(captured_at=(NOW+timedelta(minutes=3)).isoformat())):
            p=payload();mutate(p['reports'][0])
            with self.assertRaises(ValueError):convert(p)

    def test_unknown_or_duplicate_metrics_and_formatted_numbers_are_not_guessed(self):
        for fields in ([],['unknown_metric'],['stat_cost_for_roi2']*2):
            p=payload();p['reports'][0]['query']['fields']=fields
            with self.assertRaises(ValueError):convert(p)
        for value in ('1,000.00','10%','--',True,-1,10**1000,float('nan'),float('inf')):
            p=payload();p['reports'][0]['response']['data']['stat_cost_for_roi2']=value
            with self.assertRaises(ValueError):convert(p)

    def test_plain_decimal_strings_null_and_zero_are_preserved(self):
        p=payload();d=p['reports'][0]['response']['data'];d.update(stat_cost_for_roi2='0.00',total_prepay_and_pay_order_roi2=0,total_pay_order_gmv_for_roi2=None)
        m=convert(p)['snapshots'][0]['metrics']
        self.assertEqual(m['spend'],0);self.assertEqual(m['roi'],0);self.assertIsNone(m['official_actual_paid'])
        d['total_pay_order_count_for_roi2']='10.0'
        self.assertIs(type(convert(p)['snapshots'][0]['metrics']['official_deal_orders']),int)
        d['total_pay_order_count_for_roi2']='10.5'
        with self.assertRaises(ValueError):convert(p)

    def test_only_requested_verified_fields_are_retained_and_raw_response_is_dropped(self):
        p=payload();p['reports'][0]['query']['fields']=['stat_cost_for_roi2']
        p['reports'][0]['response']['data']['private_debug_text']='PRIVATE_TRACE'
        out=convert(p);self.assertEqual(out['snapshots'][0]['metrics'],{'spend':100})
        text=json.dumps(out,ensure_ascii=False)
        for value in ('PRIVATE_TRACE','PRIVATE_REQUEST_ID','not persisted'):self.assertNotIn(value,text)

    def test_capture_and_end_time_changes_have_correct_series_identity(self):
        p=payload();q=copy.deepcopy(p);q['reports'][0]['captured_at']=(NOW-timedelta(minutes=10)).isoformat()
        q['reports'][0]['query']['end_time']=(NOW-timedelta(minutes=10)).isoformat()
        a=convert(p)['snapshots'][0];b=convert(q)['snapshots'][0]
        self.assertEqual(a['source']['series_key'],b['source']['series_key']);self.assertNotEqual(a['id'],b['id'])

    def test_all_query_dimensions_and_field_sets_are_separate_series(self):
        a=convert()['snapshots'][0]['source']['series_key']
        for change in ({'adlab_scene':'UNI_PROJECT'},{'data_period':'OVER_ALL_DATA'}, {'marketing_goal':'ALL'},
                       {'order_platform':'ALL'}, {'fields':['stat_cost_for_roi2']}, {'start_time':NOW.replace(hour=1).isoformat()}):
            p=payload();p['reports'][0]['query'].update(change)
            self.assertNotEqual(a,convert(p)['snapshots'][0]['source']['series_key'])

    def test_batch_is_validated_atomically_and_identical_frames_deduplicate(self):
        p=payload();p['reports']*=2
        out=convert(p);self.assertEqual(out['received_reports'],2);self.assertEqual(out['normalized_snapshots'],1)
        p=payload();bad=copy.deepcopy(p['reports'][0]);bad['response']['code']=400;p['reports'].append(bad)
        with self.assertRaises(ValueError):convert(p)

    def test_conflicting_same_reported_data_time_is_rejected_even_if_capture_differs(self):
        p=payload();p['reports'][0]['data_at']=(NOW-timedelta(minutes=10)).isoformat()
        other=copy.deepcopy(p['reports'][0]);other['captured_at']=(NOW-timedelta(minutes=5)).isoformat();other['response']['data']['stat_cost_for_roi2']=101
        p['reports'].append(other)
        with self.assertRaises(ValueError):convert(p)

    def test_preview_digest_binds_changed_value_query_and_data_timestamp(self):
        original=convert()['preview_digest']
        for mutate in (lambda f:f['response']['data'].update(stat_cost_for_roi2=101),lambda f:f['query'].update(data_period='UNI_DATA'),lambda f:f.update(data_at=NOW.isoformat())):
            p=payload();mutate(p['reports'][0]);self.assertNotEqual(convert(p)['preview_digest'],original)

    def test_imported_report_never_certifies_history_guarantee_or_full_account_scope(self):
        s=convert()['snapshots'][0]
        self.assertFalse(s['control']['history_known']);self.assertFalse(s['control']['guarantee_verified'])
        self.assertIsNone(s['control']['running']);self.assertEqual(s['source']['coverage'],'visible_only')
        self.assertTrue(s['quality']['issues']);self.assertEqual(s['promotion_type'],'chengfang')
        p=payload();p['reports'][0]['control']={'history_known':True}
        with self.assertRaises(ValueError):convert(p)

    def test_refund_unit_unknown_cannot_become_percentage_or_emergency(self):
        s=convert()['snapshots'][0];self.assertEqual(s['metrics']['official_refund_rate_1h_raw'],.05)
        self.assertNotIn('refund_rate_1h',s['metrics']);self.assertEqual(next(v for v in official_evidence(s)['values'] if v['metric']=='official_refund_rate_1h_raw')['unit'],'来源原值')

    def test_repeated_day_reports_cannot_create_hourly_roi_from_rounded_point_values(self):
        p=payload();p['reports']=[]
        for hour in range(4):
            f=payload()['reports'][0];at=NOW-timedelta(hours=3-hour)
            f.update(captured_at=at.isoformat(),data_at=at.isoformat());f['query']['end_time']=at.isoformat()
            f['response']['data'].update(stat_cost_for_roi2=100*(hour+1),total_prepay_and_pay_order_roi2=3+hour)
            p['reports'].append(f)
        rows=convert(p)['snapshots'];evaluation=evaluate_all(rows,settings(),NOW)
        self.assertFalse(any(w['complete'] for e in evaluation['entities'] for w in e['hourly']))
        self.assertFalse(any(r['eligible'] for r in evaluation['recommendations']))

    def test_source_evidence_report_and_ai_preserve_units_without_raw_response(self):
        out=convert();rows=out['snapshots'];e=evaluate_all(rows,settings(),NOW)
        report=build_report(rows,settings(),e,'123',NOW);source=report['evidence_and_risks']['sources'][0]
        self.assertEqual(source['official_report']['query']['data_period'],'ALL_DATA')
        self.assertIn('official_actual_paid',report_markdown(report))
        pack=make_package(rows,e);text=json.dumps(pack,allow_nan=False)
        self.assertNotIn('PRIVATE_REQUEST_ID',text);self.assertIn('official_total_deal_amount',text)

    def test_raw_file_parser_rejects_duplicate_fields_and_nonfinite_values(self):
        raw=json.dumps(payload())
        for text in (raw.replace('"code": 0','"code": 0, "code": 1'),raw.replace('"stat_cost_for_roi2": 100','"stat_cost_for_roi2": NaN'),'{not json}'):
            with self.assertRaises(ValueError):convert({'document_text':text})
        self.assertEqual(convert({'document_text':raw})['preview_digest'],convert()['preview_digest'])

    def test_raw_file_wrapper_cannot_hide_headers_or_own_digest(self):
        for value in ({'document_text':json.dumps(payload()),'headers':{}}, {'document_text':{}},
                      {'document_text':json.dumps({**payload(),'preview_digest':'wrong'})}):
            with self.assertRaises(ValueError):convert(value)

    def test_deep_payload_and_missing_or_oversize_batches_are_rejected(self):
        p=payload();nested={};cursor=nested
        for _ in range(25):cursor['next']={};cursor=cursor['next']
        p['reports'][0]['response']['data']['nested']=nested
        with self.assertRaises(ValueError):convert(p)
        for reports in ([],None,[{}]*1001):
            p=payload();p['reports']=reports
            with self.assertRaises(ValueError):convert(p)

    def test_configuration_input_and_response_are_unchanged(self):
        p=payload();cfg=settings();cfg['accounts'][0]['target_roi']=None;before=copy.deepcopy((p,cfg))
        result=convert(p,cfg);self.assertEqual((p,cfg),before);json.dumps(result,ensure_ascii=False,allow_nan=False)
        self.assertIsNone(cfg['accounts'][0]['target_roi']);self.assertFalse(result['executed'])


if __name__=='__main__':unittest.main()
