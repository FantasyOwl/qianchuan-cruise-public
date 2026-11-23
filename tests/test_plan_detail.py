import copy
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from monitor.plan_detail import (PlanDetailClient, PlanDetailPermit, compare_supplied_detail,
                                 prepare_detail_request, _get_plan_detail)
from monitor.submission import AttemptLedger, BudgetClient, BudgetPermit
from test_plan_readback import inputs, PLAN
from test_engine import NOW


GOAL='AD_CONVERT_TYPE_LIVE_PAY_ROI'
CFG={'accounts':[{'id':'123','scene':'live','observe':True}]}


def detail_inputs(action='budget_up'):
    _,query,attempt=inputs(action)
    delivery={'budget_mode':'BUDGET_MODE_DAY','budget':1200,'roi2_goal':3.32,
              'smart_bid_type':'SMART_BID_CUSTOM','deep_external_action':GOAL}
    data={'ad_id':int(PLAN),'marketing_goal':'LIVE_PROM_GOODS','adlab_scene':0,
          'delivery_setting':delivery,'opt_status':'DISABLE' if action=='pause_plan' else 'ENABLE',
          'status':'OFFLINE_BALANCE'}
    if action in {'roi_target_up','roi_target_down'}:
        attempt['context'].update(before=3.5,after=3.32 if action=='roi_target_down' else 3.68,
                                  metric_basis='overall_payment',plan_delivery_mode='cost_control',
                                  plan_optimization_goal=GOAL)
        delivery['roi2_goal']=attempt['context']['after']
    return {'code':0,'data':data},query,attempt


def compare(raw,query,attempt):
    return compare_supplied_detail(json.dumps(raw),query,attempt,NOW)


class DetailComparisonTests(unittest.TestCase):
    def test_native_nested_budget_and_exact_id_use_current_detail_contract(self):
        raw,q,a=detail_inputs();raw['data']['budget']=1
        result=compare(raw,q,a)
        self.assertEqual(result['status'],'matches_requested_value')
        self.assertEqual(result['observed'],'1200.00');self.assertEqual(result['entity_id'],PLAN)
        self.assertEqual(result['api_path'],'/open_api/v1.0/qianchuan/uni_promotion/ad/detail/')
        self.assertNotIn('sdk_revision',result)
        for field in ('provenance_verified','platform_state_observed','actual_delivery_confirmed',
                      'execution_confirmed','can_release_hold','automatic_retry'):
            self.assertFalse(result[field])

    def test_roi_up_and_down_match_observed_plan_goal_not_account_assessment(self):
        for action in ('roi_target_up','roi_target_down'):
            raw,q,a=detail_inputs(action);result=compare(raw,q,a)
            self.assertEqual(result['status'],'matches_requested_value')
            raw['data']['delivery_setting']['roi2_goal']=2
            self.assertEqual(compare(raw,q,a)['status'],'differs_requested_value')

    def test_net_roi_requires_explicit_matching_optimization_not_marketing(self):
        raw,q,a=detail_inputs('roi_target_down')
        a['context'].update(metric_basis='net',plan_optimization_goal='AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI')
        self.assertEqual(compare(raw,q,a)['status'],'inconclusive')
        raw['data']['delivery_setting']['deep_external_action']='AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI'
        self.assertEqual(compare(raw,q,a)['status'],'matches_requested_value')
        a['context']['metric_basis']='marketing'
        self.assertEqual(compare(raw,q,a)['status'],'inconclusive')

    def test_delivery_mode_product_and_original_direction_cannot_be_borrowed(self):
        raw,q,a=detail_inputs('roi_target_down')
        changes=[lambda r,a:r['data']['delivery_setting'].update(smart_bid_type='SMART_BID_CONSERVATIVE'),
                 lambda r,a:a['context'].update(plan_delivery_mode='volume'),
                 lambda r,a:r['data'].update(adlab_scene=True),lambda r,a:r['data'].update(adlab_scene=9),
                 lambda r,a:a['context'].update(after=3.49),lambda r,a:a['context'].update(after=3.8)]
        for change in changes:
            r,attempt=copy.deepcopy((raw,a));change(r,attempt)
            self.assertEqual(compare(r,q,attempt)['status'],'inconclusive')

    def test_switch_does_not_establish_actual_delivery_or_merge_special_disables(self):
        raw,q,a=detail_inputs('resume_plan')
        self.assertEqual(compare(raw,q,a)['status'],'matches_requested_value')
        self.assertFalse(compare(raw,q,a)['actual_delivery_confirmed'])
        raw,q,a=detail_inputs('pause_plan');raw['data']['opt_status']='ROI2_DISABLE'
        self.assertEqual(compare(raw,q,a)['status'],'differs_requested_value')

    def test_unrelated_lossy_ids_and_wrong_nested_shapes_are_rejected(self):
        raw,q,a=detail_inputs()
        for identity in (9,float(PLAN),True,None):
            r=copy.deepcopy(raw);r['data']['ad_id']=identity
            with self.assertRaises(ValueError):compare(r,q,a)
        raw['data']['delivery_setting']=[]
        with self.assertRaises(ValueError):compare(raw,q,a)

    def test_no_fallback_to_root_budget_and_no_rounding_roi(self):
        raw,q,a=detail_inputs();raw['data']['delivery_setting'].pop('budget');raw['data']['budget']=1200
        self.assertEqual(compare(raw,q,a)['status'],'inconclusive')
        raw,q,a=detail_inputs('roi_target_down');raw['data']['delivery_setting']['roi2_goal']='3.3201'
        self.assertEqual(compare(raw,q,a)['status'],'inconclusive')

    def test_names_creatives_modify_time_and_retired_stats_are_not_retained(self):
        raw,q,a=detail_inputs()
        raw['data'].update(name='private-name',modify_time='private-time',
                           creative_setting={'title':'private-title'},stats_info={'stat_cost':99999})
        text=json.dumps(compare(raw,q,a))
        for private in ('private-name','private-time','private-title','99999','modify_time'):
            self.assertNotIn(private,text)

    def test_duplicate_json_credentials_and_non_success_do_not_certify_values(self):
        raw,q,a=detail_inputs()
        texts=[json.dumps({**raw,'authorization':'private-secret'}),
               '{"code":0,"code":1,"data":{}}',json.dumps({**raw,'code':False}),
               '{"code":0,"data":{"ad_id":8012367229158357,"x":NaN}}']
        for text in texts:
            with self.assertRaises(ValueError) as caught:compare_supplied_detail(text,q,a,NOW)
            self.assertNotIn('private-secret',str(caught.exception))


class DetailClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'attempts.db';self.ledger=AttemptLedger(self.path)
        self.raw,self.query,self.attempt=detail_inputs('roi_target_down')
        self.ledger.claim('one','original-digest',NOW-timedelta(minutes=3),self.attempt['context'])
        self.ledger.finish('one','unknown',{'state':'unknown'})
        request=prepare_detail_request('123',PLAN,CFG)
        self.permit=PlanDetailPermit('123',request['digest'],NOW,NOW+timedelta(minutes=2),'mock-only-read-review')

    def client(self,transport):
        return PlanDetailClient(enable_network=True,transport=transport,clock=lambda:NOW)

    def test_default_disabled_before_ledger_token_and_transport(self):
        ledger,token,transport=Mock(),Mock(),Mock()
        with self.assertRaises(PermissionError):
            PlanDetailClient(transport=transport).review_attempt(ledger,'one',CFG,None,token)
        ledger.connect.assert_not_called();token.assert_not_called();transport.assert_not_called()

    def test_mock_read_compares_and_persists_but_unknown_hold_survives_restart(self):
        transport=Mock(return_value=json.dumps(self.raw))
        result=self.client(transport).review_attempt(self.ledger,'one',CFG,self.permit,lambda:'mock-secret')
        self.assertTrue(result['ok']);self.assertEqual(result['transport_kind'],'custom_transport')
        self.assertEqual(result['comparison']['status'],'matches_requested_value')
        self.assertFalse(result['submission_state_changed']);self.assertFalse(result['comparison']['can_release_hold'])
        transport.assert_called_once_with('123',PLAN,'mock-secret',10)
        restarted=AttemptLedger(self.path)
        with restarted.connect() as db:
            self.assertEqual(db.execute('SELECT state FROM submission_attempts').fetchone()[0],'unknown')
            self.assertEqual(db.execute('SELECT count(*) FROM supplied_readback_reviews').fetchone()[0],1)
        with self.assertRaisesRegex(ValueError,'未明确'):
            restarted.claim('new-action','different-digest',NOW+timedelta(hours=2),self.attempt['context'])
        self.assertNotIn(b'mock-secret',self.path.read_bytes())

    def test_detail_review_is_idempotent_and_not_confused_with_list_schema(self):
        transport=Mock(return_value=json.dumps(self.raw));client=self.client(transport)
        first=client.review_attempt(self.ledger,'one',CFG,self.permit,lambda:'test')
        second=client.review_attempt(self.ledger,'one',CFG,self.permit,lambda:'test')
        self.assertEqual(first['comparison'],second['comparison'])
        with self.assertRaises(ValueError):
            self.ledger.review_supplied_readback('one',json.dumps(self.raw),self.query,NOW,response_kind='list')
        with self.ledger.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM supplied_readback_reviews').fetchone()[0],1)

    def test_mismatched_or_writer_permit_never_reads_credentials(self):
        request=prepare_detail_request('123',PLAN,CFG);token,transport=Mock(),Mock()
        permits=[None,{'approved':True},BudgetPermit('123',request['digest'],NOW,NOW+timedelta(minutes=2),'mock'),
                 PlanDetailPermit('456',request['digest'],NOW,NOW+timedelta(minutes=2),'mock'),
                 PlanDetailPermit('123','wrong',NOW,NOW+timedelta(minutes=2),'mock'),
                 PlanDetailPermit('123',request['digest'],NOW-timedelta(minutes=10),NOW,'mock')]
        for permit in permits:
            with self.assertRaises(PermissionError):self.client(transport).review_attempt(self.ledger,'one',CFG,permit,token)
        token.assert_not_called();transport.assert_not_called()

    def test_account_removed_paused_unknown_or_future_attempt_fails_before_token(self):
        token,transport=Mock(),Mock()
        for cfg in ({'accounts':[]},{'accounts':[{'id':'123','scene':'live','observe':False}]},
                    {'accounts':[{'id':'123','scene':'unknown'}]}):
            with self.assertRaises(ValueError):self.client(transport).review_attempt(self.ledger,'one',cfg,self.permit,token)
        with self.assertRaises(ValueError):self.client(transport).review_attempt(self.ledger,'missing',CFG,self.permit,token)
        with self.ledger.connect() as db:db.execute('UPDATE submission_attempts SET created_at=?',((NOW+timedelta(minutes=1)).isoformat(),))
        with self.assertRaises(ValueError):self.client(transport).review_attempt(self.ledger,'one',CFG,self.permit,token)
        token.assert_not_called();transport.assert_not_called()

    def test_token_delay_expiry_and_account_pause_are_rechecked_before_io(self):
        transport=Mock();clock=Mock(side_effect=[NOW,NOW+timedelta(minutes=3)])
        with self.assertRaises(PermissionError):
            PlanDetailClient(enable_network=True,transport=transport,clock=clock).review_attempt(self.ledger,'one',CFG,self.permit,lambda:'test')
        cfg=copy.deepcopy(CFG)
        def token():cfg['accounts'][0]['observe']=False;return 'test'
        with self.assertRaises(ValueError):self.client(transport).review_attempt(self.ledger,'one',cfg,self.permit,token)
        transport.assert_not_called()

    def test_timeout_and_invalid_payload_are_redacted_and_never_retry_or_save(self):
        transports=[Mock(side_effect=TimeoutError('private-secret')),Mock(return_value='{"token":"private-secret"}')]
        for transport in transports:
            result=self.client(transport).review_attempt(self.ledger,'one',CFG,self.permit,lambda:'test')
            self.assertFalse(result['ok']);self.assertFalse(result['automatic_retry'])
            self.assertFalse(result['comparison_saved']);self.assertNotIn('private-secret',json.dumps(result))
            self.assertEqual(transport.call_count,1)
        with self.ledger.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM supplied_readback_reviews').fetchone()[0],0)
        self.assertNotIn(b'private-secret',self.path.read_bytes())

    def test_explicit_falsey_transports_are_used_and_noncallable_transports_rejected(self):
        raw=json.dumps(self.raw)
        class FalseyTransport:
            calls=0
            def __bool__(self):return False
            def __call__(self,*args):self.calls+=1;return raw
        transport=FalseyTransport()
        with patch('monitor.plan_detail._get_plan_detail',side_effect=AssertionError('must not use default')):
            result=self.client(transport).review_attempt(self.ledger,'one',CFG,self.permit,lambda:'test')
        self.assertTrue(result['ok']);self.assertEqual(transport.calls,1)
        for constructor in (lambda:PlanDetailClient(transport=False),lambda:BudgetClient(self.ledger,transport=False)):
            with self.assertRaises(ValueError):constructor()

    def test_falsey_writer_transport_does_not_fall_back_to_network(self):
        from test_submission import fixture
        rows,cfg,proposal,permit=fixture()
        class FalseyTransport:
            calls=0
            def __bool__(self):return False
            def __call__(self,*args):
                self.calls+=1;return {'code':0,'data':{'results':[{'ad_id':int(PLAN),'status':'SUCCESS'}]}}
        transport=FalseyTransport()
        with patch.object(BudgetClient,'default_transport',side_effect=AssertionError('must not use default')) as default:
            result=BudgetClient(self.ledger,enable_network=True,transport=transport,clock=lambda:NOW).dispatch('writer',proposal['id'],rows,cfg,permit,lambda:'test',NOW)
        self.assertEqual(result['state'],'confirmed');self.assertEqual(transport.calls,1);default.assert_not_called()

    def test_fixed_get_query_exact_ids_no_proxy_redirect_or_oversize_body(self):
        returned=Mock();returned.__enter__=Mock(return_value=returned);returned.__exit__=Mock(return_value=False)
        expected='https://api.oceanengine.com/open_api/v1.0/qianchuan/uni_promotion/ad/detail/?advertiser_id=123&ad_id='+PLAN
        returned.geturl.return_value=expected;returned.read.return_value=json.dumps(self.raw).encode()
        opener=Mock();opener.open.return_value=returned
        with patch('monitor.plan_detail.urllib.request.build_opener',return_value=opener) as build:
            _get_plan_detail('123',PLAN,'test',10)
            request=opener.open.call_args.args[0]
            self.assertEqual(request.full_url,expected);self.assertEqual(request.method,'GET');self.assertIsNone(request.data)
            self.assertIsNone(build.call_args.args[1].redirect_request(None,None,None,None,None,None))
            returned.geturl.return_value='https://example.invalid/'
            with self.assertRaises(ValueError):_get_plan_detail('123',PLAN,'test',10)
            returned.geturl.return_value=expected;returned.read.return_value=b' '*1048577
            with self.assertRaises(ValueError):_get_plan_detail('123',PLAN,'test',10)


if __name__=='__main__':unittest.main()
