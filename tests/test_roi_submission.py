import copy
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from monitor.actuation import prepare_action, interpret_roi_response, interpret_budget_response
from monitor.evaluation import evaluate_all
from monitor.schema import normalize_snapshot
from monitor.server import App, Handler, Server
from monitor.ai import make_package
from monitor.submission import AttemptLedger, RoiTargetClient, RoiTargetPermit, BudgetPermit, PlanStatusPermit, _post_roi
from test_actuation import fixture as preview_fixture, NOW as PREVIEW_NOW
from test_engine import NOW, settings
from test_roi_tuning import down_rows


PLAN = '8012367229158357'
GOAL = 'AD_CONVERT_TYPE_LIVE_PAY_ROI'


def preview(basis='overall_payment'):
    cfg, row, proposal = preview_fixture()
    cfg['accounts'][0]['roi_basis']=basis
    cfg['accounts'][0]['scene']='live'
    row.update(scene='live',metric_basis=basis)
    row['source']['metric_period']='day'
    row['control']['running']=True
    row['metrics']['target_roi']=3.5
    row['context'].update(manual_reviewed=True,plan_delivery_mode='cost_control',plan_roi_mode='roi_target',
                          plan_optimization_goal=GOAL if basis=='overall_payment' else 'AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI',
                          plan_roi_basis=basis,plan_roi_config_verified=True,plan_roi_observed_at=PREVIEW_NOW.isoformat(),
                          plan_roi_step=.01,plan_roi_min=.01,plan_roi_max=100)
    proposal.update(action='roi_target_down',before=3.5,after=3.32,metric_basis=basis)
    return cfg,row,proposal


def dispatch_fixture():
    cfg=settings();rows=down_rows()
    for row in rows:
        if row['scope']=='plan':
            row['entity_id']=PLAN
            row['context'].update(plan_id=PLAN,plan_delivery_mode='cost_control',plan_optimization_goal=GOAL)
    proposal=next(p for p in evaluate_all(rows,cfg,NOW)['recommendations'] if p['action']=='roi_target_down')
    latest=next(r for r in reversed(rows) if r['scope']=='plan')
    prepared=prepare_action(proposal,latest,cfg,NOW)
    if not prepared['supported_for_preview']:raise AssertionError(prepared['blockers'])
    permit=RoiTargetPermit('123',prepared['digest'],NOW,NOW+timedelta(minutes=2),'mock-only-explicit-review')
    return rows,cfg,proposal,permit


def response(status='SUCCESS'):
    return {'code':0,'data':{'results':[{'ad_id':int(PLAN),'status':status}]}}


class RoiPreviewTests(unittest.TestCase):
    def test_current_plan_goal_is_distinct_from_assessment_and_exact_request_is_local(self):
        cfg,row,p=preview();result=prepare_action(p,row,cfg,PREVIEW_NOW)
        self.assertTrue(result['supported_for_preview']);self.assertFalse(result['live_execution_allowed'])
        self.assertFalse(result['request']['submitted'])
        body=json.loads(result['request']['body_json'])
        self.assertEqual(body,{'advertiser_id':int(row['account_id']),'update_roi2_infos':[
            {'ad_id':int(PLAN),'roi2_goal':3.32,'deep_external_action':GOAL}]})
        self.assertNotEqual(cfg['accounts'][0]['target_roi'],p['before'])

    def test_net_goal_is_explicit_and_marketing_is_not_mapped_to_payment(self):
        cfg,row,p=preview('net')
        result=prepare_action(p,row,cfg,PREVIEW_NOW)
        self.assertTrue(result['supported_for_preview'])
        self.assertEqual(json.loads(result['request']['body_json'])['update_roi2_infos'][0]['deep_external_action'],
                         'AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI')
        for basis in ('marketing','settled_7d','control_payment','unknown'):
            cfg,row,p=preview(basis)
            self.assertFalse(prepare_action(p,row,cfg,PREVIEW_NOW)['supported_for_preview'])

    def test_missing_mode_goal_or_verified_input_never_creates_request(self):
        cfg,row,p=preview()
        for key in ('plan_delivery_mode','plan_optimization_goal','plan_roi_mode','plan_roi_basis',
                    'plan_roi_config_verified','plan_roi_observed_at','plan_roi_step','plan_roi_min','plan_roi_max','manual_reviewed'):
            bad=copy.deepcopy(row);bad['context'].pop(key)
            result=prepare_action(p,bad,cfg,PREVIEW_NOW)
            self.assertFalse(result['supported_for_preview'],key);self.assertIsNone(result['request'])

    def test_volume_wrong_optimization_and_unknown_products_are_held(self):
        cfg,row,p=preview()
        for fields in ({'plan_delivery_mode':'volume'},{'plan_optimization_goal':'AD_CONVERT_TYPE_LIVE_PURE_PAY_ROI'},
                       {'plan_roi_mode':'conversion_cost'},{'plan_roi_observed_at':(PREVIEW_NOW-timedelta(minutes=2)).isoformat()}):
            bad=copy.deepcopy(row);bad['context'].update(fields)
            self.assertFalse(prepare_action(p,bad,cfg,PREVIEW_NOW)['supported_for_preview'])
        for product in ('standard','search','unknown'):
            bad=copy.deepcopy(row);bad['promotion_type']=product
            self.assertFalse(prepare_action(p,bad,cfg,PREVIEW_NOW)['supported_for_preview'])

    def test_two_decimal_contract_never_rounds_a_finer_semantic_candidate(self):
        cfg,row,p=preview();row['context']['plan_roi_step']=.001
        p.update(action='roi_target_up',after=3.675)
        result=prepare_action(p,row,cfg,PREVIEW_NOW)
        self.assertFalse(result['supported_for_preview']);self.assertIsNone(result['request'])
        p['after']=3.68
        self.assertTrue(prepare_action(p,row,cfg,PREVIEW_NOW)['supported_for_preview'])

    def test_direction_bounds_grid_state_and_real_identity_are_rechecked(self):
        cfg,row,p=preview()
        mutations=[lambda r,p:p.update(before=2.5),lambda r,p:p.update(after=3.49),
                   lambda r,p:p.update(after=3.0),lambda r,p:p.update(after=3.8),
                   lambda r,p:r['context'].update(plan_roi_min=3.4),
                   lambda r,p:r['context'].update(plan_roi_step=.03),
                   lambda r,p:r['control'].update(running=False),lambda r,p:r['source'].update(kind='demo'),
                   lambda r,p:r.update(scope='control'),lambda r,p:p.update(target_id='999')]
        for mutate in mutations:
            bad,proposal=copy.deepcopy((row,p));mutate(bad,proposal)
            self.assertFalse(prepare_action(proposal,bad,cfg,PREVIEW_NOW)['supported_for_preview'])

    def test_ingestion_preserves_enums_without_granting_browser_configuration_certification(self):
        cfg,row,_=preview()
        imported=normalize_snapshot(row,cfg,PREVIEW_NOW)
        self.assertEqual(imported['context']['plan_optimization_goal'],GOAL)
        row['source'].update(kind='browser_dom',url='https://qianchuan.jinritemai.com/uni-prom?aavid='+row['account_id'])
        browser=normalize_snapshot(row,cfg,PREVIEW_NOW)
        self.assertIsNone(browser['context']['plan_roi_config_verified'])
        self.assertIsNone(browser['context']['manual_reviewed'])
        for key,value in (('plan_delivery_mode','fast'),('plan_optimization_goal','PAY_ROI')):
            bad=copy.deepcopy(row);bad['context'][key]=value
            with self.assertRaises(ValueError):normalize_snapshot(bad,cfg,PREVIEW_NOW)


class RoiResponseTests(unittest.TestCase):
    def test_api_success_is_per_plan_and_not_execution_or_a_flag(self):
        self.assertTrue(interpret_roi_response(response(),[PLAN])['success'])
        self.assertFalse(interpret_roi_response(response(),[PLAN])['executed'])
        self.assertEqual(interpret_roi_response(response('FAILED'),[PLAN])['failed'],[PLAN])
        bad=response();bad['data']['results'][0]={'ad_id':int(PLAN),'flag':True}
        self.assertEqual(interpret_roi_response(bad,[PLAN])['unknown'],[PLAN])

    def test_wrong_or_lossy_ids_duplicates_and_success_error_conflicts_stay_unknown(self):
        cases=[]
        for identity in (True,float(PLAN),'0','83213982241676983'):
            bad=response();bad['data']['results'][0]['ad_id']=identity;cases.append(bad)
        bad=response();bad['data']['results'].append({'ad_id':9,'status':'SUCCESS'});cases.append(bad)
        bad=response();bad['data']['results']*=2;cases.append(bad)
        bad=response();bad['data']['results'][0]['error_message']='conflicting private error';cases.append(bad)
        cases.extend([{'code':True,'data':{}},{'code':0,'data':{}}, {'code':0,'data':{'results':[None]}}])
        for value in cases:
            for decoder in (interpret_roi_response,interpret_budget_response):
                result=decoder(value,[PLAN])
                self.assertFalse(result['success']);self.assertEqual(result['unknown'],[PLAN])
                self.assertNotIn('private error',json.dumps(result))

    def test_batch_partial_failure_missing_ids_and_bound_are_explicit(self):
        value={'code':0,'data':{'results':[{'ad_id':1,'status':'SUCCESS'},{'ad_id':2,'status':'FAILED'}]}}
        result=interpret_roi_response(value,['1','2','3'])
        self.assertEqual(result['confirmed'],['1']);self.assertEqual(result['failed'],['2']);self.assertEqual(result['unknown'],['3'])
        for ids in ([],[str(x) for x in range(1,12)]):
            with self.assertRaises(ValueError):interpret_roi_response(value,ids)


class RoiIntegrationTests(unittest.TestCase):
    def test_current_evaluation_reports_contract_and_anonymous_ai_keeps_mode_evidence(self):
        rows,cfg,_,_=dispatch_fixture();result=evaluate_all(rows,cfg,NOW)
        proposal=next(p for p in result['recommendations'] if p['action']=='roi_target_down')
        refs={r['id']:r for r in proposal['policy_basis']['official_references']}
        self.assertEqual(refs['contract_roi']['checked_at'],'2026-10-23')
        self.assertIn('8400331305303110',refs['contract_roi']['url'])
        package=make_package(rows,result);text=json.dumps(package)
        self.assertIn(GOAL,text);self.assertIn('cost_control',text);self.assertNotIn(PLAN,text)

    def test_local_http_preview_uses_normalized_current_evidence_and_has_no_roi_submit_route(self):
        rows,cfg,_,_=dispatch_fixture()
        parsed=[]
        for row in rows:
            row['source']['dataset_id']=row['source']['series_key']
            parsed.append(normalize_snapshot(row,cfg,NOW))
        account_key=next(r['source']['series_key'] for r in parsed if r['scope']=='account')
        for row in parsed:
            if row['scope']=='plan':row['context']['account_source_key']=account_key
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'config.json').write_text(json.dumps(cfg),encoding='utf-8')
            app=App(root);app.store.add(parsed)
            server=Server(('127.0.0.1',0),Handler);app.port=server.server_address[1];server.app=app
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            with patch('monitor.server.now_cn',return_value=NOW),patch('monitor.actuation.now_cn',return_value=NOW):
                _,evaluation=app.current()
                proposal=next(p for p in evaluation['recommendations'] if p['action']=='roi_target_down')
                self.assertTrue(proposal['eligible'])
                thread.start()
                try:
                    def post(path,body):
                        request=urllib.request.Request('http://127.0.0.1:'+str(app.port)+path,
                            data=json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Monitor-CSRF':app.csrf})
                        return urllib.request.urlopen(request,timeout=5)
                    with post('/api/action-preview',{'recommendation_id':proposal['id']}) as response:
                        result=json.load(response)
                    self.assertTrue(result['supported_for_preview']);self.assertFalse(result['live_execution_allowed'])
                    self.assertEqual(json.loads(result['request']['body_json'])['update_roi2_infos'][0]['roi2_goal'],3.32)
                    with self.assertRaises(urllib.error.HTTPError) as caught:post('/api/roi-submit',{'approved':True})
                    self.assertEqual(caught.exception.code,404)
                    with app.store.connect() as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM operation_reports').fetchone()[0],0)
                        self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='submission_attempts'").fetchone()[0],0)
                finally:
                    server.shutdown();server.server_close();thread.join(5)


class RoiSubmissionTests(unittest.TestCase):
    def setUp(self):
        clock=patch('monitor.submission.now_cn',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'attempts.db';self.ledger=AttemptLedger(self.path)

    def test_default_disabled_before_credentials_evaluation_or_transport(self):
        token,transport=Mock(),Mock()
        with patch('monitor.submission.evaluate_all') as evaluate:
            with self.assertRaises(PermissionError):
                RoiTargetClient(self.ledger,transport=transport).dispatch('one','p',[],{},None,token,NOW)
            evaluate.assert_not_called()
        token.assert_not_called();transport.assert_not_called()

    def test_mock_full_evaluation_submission_records_exact_goal_without_secrets(self):
        rows,cfg,p,permit=dispatch_fixture();calls=[]
        def transport(body,token,timeout):
            calls.append(json.loads(body));self.assertEqual(timeout,10);return response()
        result=RoiTargetClient(self.ledger,enable_network=True,transport=transport).dispatch(
            'one',p['id'],rows,cfg,permit,lambda:'test-secret',NOW)
        self.assertEqual(calls[0]['update_roi2_infos'][0]['roi2_goal'],3.32)
        self.assertEqual(result['state'],'confirmed');self.assertFalse(result['platform_state_observed'])
        with self.ledger.connect() as db:stored=str(db.execute('SELECT * FROM submission_attempts').fetchall())
        self.assertIn(GOAL,stored);self.assertNotIn('test-secret',stored)

    def test_wrong_permit_class_digest_account_and_expiry_never_read_credentials(self):
        rows,cfg,p,permit=dispatch_fixture();token,transport=Mock(),Mock()
        args=(permit.account_id,permit.request_digest,NOW,NOW+timedelta(minutes=2),'mock')
        permits=[BudgetPermit(*args),PlanStatusPermit(*args),{'approved':True},
                 RoiTargetPermit('456',*args[1:]),RoiTargetPermit('123','wrong',*args[2:]),
                 RoiTargetPermit('123',permit.request_digest,NOW-timedelta(minutes=10),NOW,'mock')]
        client=RoiTargetClient(self.ledger,enable_network=True,transport=transport)
        for value in permits:
            with self.assertRaises(PermissionError):client.dispatch('one',p['id'],rows,cfg,value,token,NOW)
        token.assert_not_called();transport.assert_not_called()

    def test_changed_targets_current_configuration_or_missing_scope_prevent_dispatch(self):
        rows,cfg,p,permit=dispatch_fixture();token,transport=Mock(),Mock()
        for mutate in (lambda r,c:c['accounts'][0].update(target_roi=None),
                       lambda r,c:r[-2]['context'].update(plan_delivery_mode='volume'),
                       lambda r,c:r[-2]['context'].update(plan_roi_config_verified=False),
                       lambda r,c:r[-2]['source'].update(coverage='visible_only')):
            data,settings=copy.deepcopy((rows,cfg));mutate(data,settings)
            with self.assertRaises((ValueError,PermissionError)):
                RoiTargetClient(self.ledger,enable_network=True,transport=transport).dispatch('one',p['id'],data,settings,permit,token,NOW)
        token.assert_not_called();transport.assert_not_called()

    def test_timeout_remains_unknown_and_shared_plan_lock_survives_restart(self):
        rows,cfg,p,permit=dispatch_fixture();transport=Mock(side_effect=TimeoutError('test-secret'))
        client=RoiTargetClient(self.ledger,enable_network=True,transport=transport)
        result=client.dispatch('one',p['id'],rows,cfg,permit,lambda:'test-secret',NOW)
        self.assertEqual(result['state'],'unknown');self.assertNotIn('test-secret',json.dumps(result))
        with self.assertRaisesRegex(ValueError,'未明确'):
            AttemptLedger(self.path).claim('budget-later','different',NOW+timedelta(hours=2),{'account_id':'123','entity_id':PLAN})
        self.assertEqual(transport.call_count,1)

    def test_confirmed_roi_has_same_cross_action_minimum_hour_cooldown(self):
        rows,cfg,p,permit=dispatch_fixture()
        RoiTargetClient(self.ledger,enable_network=True,transport=lambda *a:response()).dispatch('one',p['id'],rows,cfg,permit,lambda:'test',NOW)
        with self.assertRaisesRegex(ValueError,'一小时'):
            self.ledger.claim('pause-too-soon','other',NOW+timedelta(minutes=59),{'account_id':'123','entity_id':PLAN})

    def test_fixed_official_transport_has_no_redirect_or_proxy_endpoint_override(self):
        returned=Mock();returned.__enter__=Mock(return_value=returned);returned.__exit__=Mock(return_value=False)
        returned.read.return_value=b'{"code":0}'
        opener=Mock();opener.open.return_value=returned
        with patch('monitor.submission.urllib.request.build_opener',return_value=opener) as build:
            _post_roi('{"advertiser_id":123}','test',10)
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.oceanengine.com/open_api/v1.0/qianchuan/uni_promotion/ad/roi2_goal/update/')
        self.assertEqual(request.method,'POST')
        self.assertIsNone(build.call_args.args[1].redirect_request(None,None,None,None,None,None))


if __name__=='__main__':unittest.main()
