import copy
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from monitor.actuation import prepare_action
from monitor.control_contracts import interpret_control_budget_response, interpret_control_status_response
from monitor.evaluation import evaluate_all
from monitor.plan_detail import PlanDetailClient, PlanDetailPermit, prepare_detail_request
from monitor.server import App, Handler, Server
from monitor.submission import (AttemptLedger, ControlBudgetClient, ControlBudgetPermit, ControlStatusClient,
    ControlStatusPermit, BudgetPermit, _post_control_budget, _post_control_status)
from test_chase import fixture as chase_fixture, NOW


PARENT='8012367229158357'
TASK='8753264005940210'


def fixture(pause=False, same_namespace_id=False):
    rows,cfg=chase_fixture();task=PARENT if same_namespace_id else TASK
    for row in rows:
        if row['scope']=='plan':row['entity_id']=PARENT
        if row['scope']=='control':
            row['entity_id']=task;row['context'].update(parent_plan_id=PARENT,last_chase_id=task)
            if pause:
                at=datetime.fromisoformat(row['source']['data_at'])
                extra=max(0,(at-(NOW-timedelta(hours=1))).total_seconds()/600)*50
                row['metrics']['control_spend']+=extra;row['metrics']['control_gmv']+=extra*3
        if same_namespace_id and row['scope'] in {'plan','control'}:
            row['source']['series_key']='same-report'
            if row['scope']=='control':row['context']['parent_source_key']='same-report'
    evaluation=evaluate_all(rows,cfg,NOW)
    action='pause_control' if pause else 'control_budget_up'
    proposal=next(p for p in evaluation['recommendations'] if p['action']==action and p['eligible'])
    snapshot=next(r for r in reversed(rows) if r['scope']=='control')
    prepared=prepare_action(proposal,snapshot,cfg,NOW,snapshots=rows,evaluation=evaluation)
    if not prepared['supported_for_preview']:raise AssertionError(prepared['blockers'])
    permit_type=ControlStatusPermit if pause else ControlBudgetPermit
    permit=permit_type('123',prepared['digest'],NOW,NOW+timedelta(minutes=2),'mock-only-task-review')
    return rows,cfg,evaluation,proposal,snapshot,permit


def task_response(pause=False,task=TASK):
    return {'code':0,'data':{'task_ids':[int(task)],'errors':[]}} if pause else {
        'code':0,'data':{'results':[{'task_id':int(task),'status':True}]}}


class ControlPreviewTests(unittest.TestCase):
    def test_budget_uses_exact_task_id_and_separate_response_contract(self):
        rows,cfg,e,p,s,_=fixture();out=prepare_action(p,s,cfg,NOW,snapshots=rows,evaluation=e)
        self.assertEqual(json.loads(out['request']['body_json']),{'advertiser_id':123,
            'update_budget_infos':[{'task_id':int(TASK),'budget':500}]})
        self.assertFalse(out['live_execution_allowed']);self.assertFalse(out['request']['submitted'])

    def test_pause_is_pause_not_manual_end_delete_or_enable(self):
        rows,cfg,e,p,s,_=fixture(True);out=prepare_action(p,s,cfg,NOW,snapshots=rows,evaluation=e)
        self.assertIs(p['before'],True);self.assertIs(p['after'],False)
        self.assertEqual(p['parameters']['parent_plan_id'],PARENT)
        self.assertEqual(json.loads(out['request']['body_json']),{'advertiser_id':123,'task_ids':[int(TASK)],'opt_type':'PAUSE'})
        for action in ('resume_control','delete_control','disable_control','pause_plan'):
            bad=copy.deepcopy(p);bad['action']=action
            self.assertFalse(prepare_action(bad,s,cfg,NOW,snapshots=rows,evaluation=e)['supported_for_preview'])

    def test_universal_parent_identity_scope_and_fresh_report_are_required(self):
        rows,cfg,e,p,s,_=fixture()
        self.assertFalse(prepare_action(p,s,cfg,NOW)['supported_for_preview'])
        for mutate in (lambda x:x['context'].update(parent_plan_id='99'),
                       lambda x:x.update(promotion_type='chengfang'),
                       lambda x:x['context'].update(parent_source_key='other-report'),
                       lambda x:x['context'].update(manual_reviewed=False)):
            bad=copy.deepcopy(s);mutate(bad)
            self.assertFalse(prepare_action(p,bad,cfg,NOW,snapshots=rows,evaluation=e)['supported_for_preview'])
        parents=copy.deepcopy(rows)
        next(r for r in reversed(parents) if r['scope']=='plan')['source']['coverage']='visible_only'
        self.assertFalse(prepare_action(p,s,cfg,NOW,snapshots=parents,evaluation=e)['supported_for_preview'])

    def test_budget_old_value_half_step_parent_cap_and_total_cap_rechecked(self):
        rows,cfg,e,p,s,_=fixture()
        mutations=[lambda p,s:p.update(before=399),lambda p,s:p.update(after=601),
                   lambda p,s:p['evidence'].update(parent_budget=450),
                   lambda p,s:s['metrics'].update(parent_chase_budget_total=1490),
                   lambda p,s:s['context'].update(latest_budget_verified=False),
                   lambda p,s:s['context'].update(parent_control_inventory_verified=False),
                   lambda p,s:p['parameters'].update(parent_plan_id='99')]
        for mutate in mutations:
            proposal,snapshot=copy.deepcopy((p,s));mutate(proposal,snapshot)
            self.assertFalse(prepare_action(proposal,snapshot,cfg,NOW,snapshots=rows,evaluation=e)['supported_for_preview'])

    def test_payment_assessment_bridge_must_be_explicit_never_borrowed_from_plan_goal(self):
        rows,cfg,_,_,_,_=fixture(True)
        for change in ({'target_metric_basis':None},{'target_metric_basis':'net'}):
            changed=copy.deepcopy(rows)
            for row in changed:
                if row['scope']=='control':row['context'].update(change);row['metrics']['target_roi']=99
            result=evaluate_all(changed,cfg,NOW)
            self.assertFalse(any(p['eligible'] for p in result['recommendations'] if p['action']=='pause_control'))

    def test_continuous_low_task_roi_uses_explicit_overall_assessment_declaration(self):
        rows,cfg,_,_,_,_=fixture()
        for row in rows:
            if row['scope']=='control':row['metrics']['control_gmv']=row['metrics']['control_spend']*.5
        result=evaluate_all(rows,cfg,NOW)
        pause=next(p for p in result['recommendations'] if p['rule']=='control_roi_low_1h')
        self.assertTrue(pause['eligible']);self.assertTrue(pause['evidence']['no_improvement_1h'])
        self.assertEqual(pause['parameters']['parent_plan_id'],PARENT)

    def test_already_paused_or_unknown_state_cannot_gain_pause_eligibility(self):
        for state in (False,None):
            rows,cfg,_,_,_,_=fixture(True)
            for row in rows:
                if row['scope']=='control':row['control']['running']=state
            result=evaluate_all(rows,cfg,NOW)
            self.assertFalse(any(p['eligible'] for p in result['recommendations'] if p['action']=='pause_control'))

    def test_pause_parent_identity_does_not_require_an_unrelated_parent_roi_formula(self):
        rows,cfg,_,_,_,_=fixture(True)
        for row in rows:
            if row['scope']=='plan':row['metric_basis']='net'
        evaluation=evaluate_all(rows,cfg,NOW)
        proposal=next(p for p in evaluation['recommendations'] if p['action']=='pause_control' and p['eligible'])
        snapshot=next(r for r in reversed(rows) if r['scope']=='control')
        result=prepare_action(proposal,snapshot,cfg,NOW,snapshots=rows,evaluation=evaluation)
        self.assertTrue(result['supported_for_preview'])


class ControlResponseTests(unittest.TestCase):
    def test_budget_requires_boolean_status_and_task_id_not_plan_status(self):
        self.assertTrue(interpret_control_budget_response(task_response(),[TASK])['success'])
        for row in ({'ad_id':int(TASK),'status':True},{'task_id':int(TASK),'status':'SUCCESS'},
                    {'task_id':int(TASK),'status':1},{'task_id':int(TASK),'status':True,'error_message':'conflict'}):
            result=interpret_control_budget_response({'code':0,'data':{'results':[row]}},[TASK])
            self.assertEqual(result['unknown'],[TASK]);self.assertFalse(result['success'])

    def test_status_success_ids_and_failure_errors_cannot_overlap_or_be_replaced(self):
        self.assertTrue(interpret_control_status_response(task_response(True),[TASK])['success'])
        values=[{'results':[{'task_id':int(TASK),'flag':True}]},
                {'task_ids':[int(TASK)],'errors':[{'task_id':int(TASK),'error_message':'private'}]},
                {'task_ids':[int(TASK),int(TASK)]},{'task_ids':[int(TASK),9]}]
        for data in values:
            result=interpret_control_status_response({'code':0,'data':data},[TASK])
            self.assertFalse(result['success']);self.assertEqual(result['unknown'],[TASK]);self.assertNotIn('private',json.dumps(result))

    def test_partial_failed_unknown_lossy_and_duplicate_rows_are_explicit(self):
        value={'code':0,'data':{'task_ids':[1],'errors':[{'task_id':2,'error_message':'reason'}]}}
        result=interpret_control_status_response(value,['1','2','3'])
        self.assertEqual((result['confirmed'],result['failed'],result['unknown']),(['1'],['2'],['3']))
        for identity in (True,float(TASK),'85834399107811352',None):
            value=task_response();value['data']['results'][0]['task_id']=identity
            self.assertEqual(interpret_control_budget_response(value,[TASK])['unknown'],[TASK])
        value=task_response();value['data']['results']*=2
        self.assertEqual(interpret_control_budget_response(value,[TASK])['unknown'],[TASK])


class ControlSubmissionTests(unittest.TestCase):
    def setUp(self):
        clock=patch('monitor.submission.now_cn',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'attempts.db';self.ledger=AttemptLedger(self.path)

    def test_default_disabled_before_credentials_and_evaluation(self):
        for cls in (ControlBudgetClient,ControlStatusClient):
            token,transport=Mock(),Mock()
            with patch('monitor.submission.evaluate_all') as evaluate:
                with self.assertRaises(PermissionError):cls(self.ledger,transport=transport).dispatch('one','p',[],{},None,token,NOW)
                evaluate.assert_not_called()
            token.assert_not_called();transport.assert_not_called()

    def test_mock_dispatches_use_full_rules_and_never_certify_actual_state(self):
        for pause in (False,True):
            with tempfile.TemporaryDirectory() as folder:
                ledger=AttemptLedger(Path(folder)/'attempts.db')
                rows,cfg,_,p,_,permit=fixture(pause)
                cls=ControlStatusClient if pause else ControlBudgetClient
                transport=Mock(return_value=task_response(pause))
                result=cls(ledger,enable_network=True,transport=transport).dispatch('one',p['id'],rows,cfg,permit,lambda:'test-secret',NOW)
                self.assertEqual(result['state'],'confirmed');self.assertFalse(result['platform_state_observed'])
                self.assertEqual(transport.call_count,1)
                with ledger.connect() as db:
                    stored=str(db.execute('SELECT * FROM submission_attempts').fetchall())
                self.assertIn(PARENT,stored);self.assertIn('control',stored);self.assertNotIn('test-secret',stored)

    def test_wrong_permit_family_or_changed_target_and_parent_never_read_token(self):
        rows,cfg,_,p,_,permit=fixture();token,transport=Mock(),Mock()
        wrong=BudgetPermit(permit.account_id,permit.request_digest,permit.issued_at,permit.expires_at,'mock')
        with self.assertRaises(PermissionError):
            ControlBudgetClient(self.ledger,enable_network=True,transport=transport).dispatch('one',p['id'],rows,cfg,wrong,token,NOW)
        for mutate in (lambda r,c:c['accounts'][0].update(target_roi=None),
                       lambda r,c:next(x for x in reversed(r) if x['scope']=='plan')['metrics'].update(budget=1000),
                       lambda r,c:next(x for x in reversed(r) if x['scope']=='control')['context'].update(parent_plan_id='9')):
            data,settings=copy.deepcopy((rows,cfg));mutate(data,settings)
            with self.assertRaises((ValueError,PermissionError)):
                ControlBudgetClient(self.ledger,enable_network=True,transport=transport).dispatch('one',p['id'],data,settings,permit,token,NOW)
        token.assert_not_called();transport.assert_not_called()

    def test_task_timeout_locks_parent_other_task_and_plan_after_restart(self):
        rows,cfg,_,p,_,permit=fixture(True);transport=Mock(side_effect=TimeoutError('test-secret'))
        result=ControlStatusClient(self.ledger,enable_network=True,transport=transport).dispatch('one',p['id'],rows,cfg,permit,lambda:'test',NOW)
        self.assertEqual(result['state'],'unknown');self.assertNotIn('test-secret',json.dumps(result))
        for context in ({'account_id':'123','entity_id':PARENT,'scope':'plan'},
                        {'account_id':'123','entity_id':'other-task','scope':'control','parent_plan_id':PARENT}):
            with self.assertRaisesRegex(ValueError,'未明确'):
                AttemptLedger(self.path).claim('next','new',NOW+timedelta(hours=2),context)

    def test_parent_cooldown_applies_to_task_and_task_cooldown_to_parent(self):
        self.ledger.claim('parent','parent-digest',NOW,{'account_id':'123','entity_id':PARENT,'scope':'plan'})
        self.ledger.finish('parent','confirmed',{})
        context={'account_id':'123','entity_id':TASK,'scope':'control','parent_plan_id':PARENT}
        with self.assertRaisesRegex(ValueError,'一小时'):self.ledger.claim('task','task-digest',NOW+timedelta(minutes=59),context)
        self.ledger.claim('task','task-digest',NOW+timedelta(hours=1),context);self.ledger.finish('task','confirmed',{})
        with self.assertRaisesRegex(ValueError,'一小时'):
            self.ledger.claim('parent-two','new-parent-digest',NOW+timedelta(hours=1,minutes=59),{'account_id':'123','entity_id':PARENT})

    def test_concurrent_sibling_tasks_share_parent_lock_but_other_accounts_do_not(self):
        barrier=threading.Barrier(2)
        def claim(number):
            ledger=AttemptLedger(self.path);barrier.wait(timeout=5)
            try:
                ledger.claim('task-'+str(number),'digest-'+str(number),NOW,
                    {'account_id':'123','entity_id':str(number),'scope':'control','parent_plan_id':PARENT})
                return 'claimed'
            except ValueError:return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:self.assertEqual(sorted(pool.map(claim,[1,2])),['blocked','claimed'])
        self.ledger.claim('other-account','other-digest',NOW,{'account_id':'456','entity_id':TASK,'scope':'control','parent_plan_id':PARENT})

    def test_missing_scope_or_parent_cannot_be_treated_as_legacy_plan(self):
        for context in ({'account_id':'123','entity_id':TASK,'scope':'control'},
                        {'account_id':'123','entity_id':TASK,'action':'pause_control'}):
            with self.assertRaises(ValueError):self.ledger.claim('one','digest',NOW,context)

    def test_legacy_numeric_aliases_cannot_bypass_actual_parent_lock(self):
        self.ledger.claim('legacy','old-digest',NOW,{'account_id':'00123','entity_id':'00'+PARENT})
        with self.assertRaisesRegex(ValueError,'未明确'):
            self.ledger.claim('task','new-digest',NOW+timedelta(hours=2),
                {'account_id':'123','entity_id':TASK,'scope':'control','parent_plan_id':PARENT})

    def test_plan_detail_reader_rejects_task_scope_before_credentials(self):
        self.ledger.claim('task','digest',NOW,{'account_id':'123','entity_id':TASK,'scope':'control','parent_plan_id':PARENT})
        cfg={'accounts':[{'id':'123','scene':'live','observe':True}]}
        request=prepare_detail_request('123',TASK,cfg);permit=PlanDetailPermit('123',request['digest'],NOW,NOW+timedelta(minutes=2),'mock')
        token,transport=Mock(),Mock()
        with self.assertRaises(ValueError):
            PlanDetailClient(enable_network=True,transport=transport,clock=lambda:NOW).review_attempt(self.ledger,'task',cfg,permit,token)
        token.assert_not_called();transport.assert_not_called()

    def test_fixed_task_transports_have_distinct_paths_and_no_redirect(self):
        for fn,part in ((_post_control_budget,'budget'),(_post_control_status,'status')):
            returned=Mock();returned.__enter__=Mock(return_value=returned);returned.__exit__=Mock(return_value=False);returned.read.return_value=b'{"code":0}'
            opener=Mock();opener.open.return_value=returned
            with patch('monitor.submission.urllib.request.build_opener',return_value=opener) as build:fn('{}','test',10)
            request=opener.open.call_args.args[0]
            self.assertIn('/control_task/'+part+'/update/',request.full_url)
            self.assertEqual(request.method,'POST');self.assertIsNone(build.call_args.args[1].redirect_request(None,None,None,None,None,None))


class ControlHttpTests(unittest.TestCase):
    def test_http_preview_preserves_scope_when_plan_and_task_have_same_id_and_source(self):
        rows,cfg,_,p,_,_=fixture(same_namespace_id=True)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'config.json').write_text(json.dumps(cfg),encoding='utf-8')
            app=App(root);app.store.add(rows)
            server=Server(('127.0.0.1',0),Handler);app.port=server.server_address[1];server.app=app
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                with patch('monitor.server.now_cn',return_value=NOW),patch('monitor.actuation.now_cn',return_value=NOW):
                    request=urllib.request.Request('http://127.0.0.1:'+str(app.port)+'/api/action-preview',
                        data=json.dumps({'recommendation_id':p['id']}).encode(),headers={'Content-Type':'application/json','X-Monitor-CSRF':app.csrf})
                    with urllib.request.urlopen(request,timeout=5) as response:result=json.load(response)
                self.assertTrue(result['supported_for_preview'])
                self.assertEqual(json.loads(result['request']['body_json'])['update_budget_infos'][0]['task_id'],int(PARENT))
                self.assertFalse(result['live_execution_allowed'])
                with app.store.connect() as db:self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='submission_attempts'").fetchone()[0],0)
            finally:server.shutdown();server.server_close();thread.join(5)


if __name__=='__main__':unittest.main()
