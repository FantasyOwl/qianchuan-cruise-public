import copy
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from dataclasses import replace
from datetime import timedelta,timezone
from email.utils import format_datetime
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.parse
from unittest.mock import Mock,patch

from monitor.actuation import prepare_action
from monitor.control_deadline import DeadlineLedger
from monitor.native_control_io import digest
from monitor.smart_control import CREATE_PATH,STOP_PATH
from monitor.smart_control_runtime import (SmartControlLedger,SmartControlService,SmartControlClient,
    SmartControlDelegation)
from monitor.submission import AttemptLedger
from test_smart_control import fixture


class Wire:
    """HTTPS exchange boundary, no sockets or actual platform credentials."""
    def __init__(self,now):
        self.now=now;self.calls=[];self.create_mode='accepted';self.stop_mode='accepted'
        self.clock_mode='ok';self.hook=None;self.lock=threading.Lock()

    def opener(self,*handlers):
        wire=self
        class Opener:
            def open(self,request,timeout):
                url=urllib.parse.urlsplit(request.full_url)
                assert url.scheme=='https' and url.netloc=='api.oceanengine.com'
                assert url.path in {CREATE_PATH,STOP_PATH} and request.method=='POST' and timeout==10
                body=json.loads(request.data)
                with wire.lock:wire.calls.append((url.path,body));count=len(wire.calls)
                if wire.hook:wire.hook(url.path)
                mode=wire.create_mode if url.path==CREATE_PATH else wire.stop_mode
                if mode=='timeout':raise TimeoutError('PRIVATE_TOKEN_AND_ERROR')
                if mode=='crash':raise KeyboardInterrupt()
                payload={'code':0,'data':{'id':8753264005940210},'request_id':'rid-'+str(count),'message':'PRIVATE_MESSAGE'}
                if mode=='rejected':payload['code']=1
                if mode=='ambiguous':payload['data']=None
                if mode=='no_request_id':payload.pop('request_id')
                raw=json.dumps(payload).encode()
                date=wire.now-timedelta(hours=1) if wire.clock_mode=='stale' else wire.now
                class Response:
                    headers={'Date':format_datetime(date.astimezone(timezone.utc),usegmt=True)}
                    def __enter__(self):return self
                    def __exit__(self,*args):pass
                    def geturl(self):return request.full_url
                    def read(self,n):return raw[:n]
                return Response()
        return Opener()


class SmartControlRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'native.db';self.ledger=SmartControlLedger(self.path)
        self.rows,self.cfg,self.at,self.proposal,self.evaluation=fixture();self.cfg['accounts'][0]['observe']=True
        self.preview=prepare_action(self.proposal,self.rows[-2],self.cfg,self.at,snapshots=self.rows,evaluation=self.evaluation)
        self.assertTrue(self.preview['supported_for_preview'],self.preview['blockers'])
        self.body=self.preview['request']['body_json'];self.wire=Wire(self.at)
        self.allow_create=True;self.allow_stop=True;self.delegate_available=True;self.services=[]
        http=patch('monitor.native_control_io.urllib.request.build_opener',side_effect=self.wire.opener)
        http.start();self.addCleanup(http.stop)
        self.addCleanup(lambda:[s.stop() for s in self.services])

    def reserve(self,run_id='flow',minutes=30,body=None):
        return self.ledger.reserve(run_id,body or self.body,minutes,self.wire.now)

    def delegation(self,record):
        if not self.delegate_available:return None
        return SmartControlDelegation(record['id'],record['account_id'],record['parent_plan_id'],record['creation_request_digest'],
            record['duration_minutes'],self.at,self.at+timedelta(hours=3),'PRIVATE_AUTHORIZATION',self.allow_create,self.allow_stop)

    def service(self,ledger=None,start=False,token_provider=None):
        s=SmartControlService(ledger or self.ledger,lambda:copy.deepcopy(self.cfg),self.delegation,
            token_provider or (lambda record:'PRIVATE_TOKEN'),enable_network=True,clock=lambda:self.wire.now,interval=30)
        self.services.append(s)
        if start:self.assertTrue(s.start()['thread_alive'])
        return s

    def create(self,service=None,provider=None):
        if service is None:
            self.reserve();service=self.service(start=True)
        client=SmartControlClient(self.ledger,service,enable_network=True,clock=lambda:self.wire.now)
        return client.dispatch('flow',self.proposal['id'],self.rows,self.cfg,provider or (lambda:'PRIVATE_TOKEN'))

    def stops(self):return [c for c in self.wire.calls if c[0]==STOP_PATH]

    def test_default_off_client_and_worker_do_not_touch_clock_ledger_or_credentials(self):
        clock=Mock(side_effect=AssertionError());ledger=Mock();provider=Mock()
        with self.assertRaises(PermissionError):SmartControlClient(ledger,None,clock=clock).dispatch('x','x',None,None,provider)
        service=SmartControlService(ledger,None,None,None,clock=clock)
        for call in (service.start,service.run_once,lambda:service.step('x')):
            with self.assertRaises(PermissionError):call()
        clock.assert_not_called();provider.assert_not_called();self.assertEqual(ledger.mock_calls,[])

    def test_live_worker_and_same_database_are_required_before_credentials(self):
        self.reserve();provider=Mock();service=self.service()
        with self.assertRaises(PermissionError):self.create(service,provider)
        provider.assert_not_called();self.assertEqual(self.wire.calls,[])
        service=self.service(ledger=SmartControlLedger(Path(self.temp.name)/'other.db'),start=True)
        with self.assertRaises(PermissionError):self.create(service,provider)
        provider.assert_not_called()

    def test_full_policy_reservation_and_native_exchange_keep_ids_and_no_native_duration(self):
        def verify_pre_io(path):
            record=SmartControlLedger(self.path).get('flow')
            self.assertEqual(record['state'],'creation_claimed')
            self.assertEqual(record['due_at'],(self.at+timedelta(minutes=30)).isoformat())
        self.wire.hook=verify_pre_io
        out=self.create();record=self.ledger.get('flow')
        self.assertEqual(out['state'],'creation_accepted_pending_readback');self.assertIsNone(out['created_task_id'])
        self.assertTrue(record['creation_receipt']['native_origin_verified']);self.assertTrue(out['parent_hold_active'])
        self.assertEqual(self.wire.calls[0],(CREATE_PATH,{'advertiser_id':9990001,'ad_id':8012367229158357,'budget':100}))
        self.assertFalse(out['platform_state_observed']);self.assertFalse(out['automatic_retry'])

    def test_receipt_delay_and_reopen_cannot_extend_first_send_deadline(self):
        self.wire.hook=lambda path:setattr(self.wire,'now',self.at+timedelta(seconds=90))
        out=self.create();record=SmartControlLedger(self.path).get('flow')
        self.assertEqual(out['due_at'],(self.at+timedelta(minutes=30)).isoformat())
        self.assertEqual(record['creation_receipt']['received_at'],self.wire.now.isoformat())
        self.assertEqual(record['first_create_request_sent_at'],self.at.isoformat())

    def test_creation_timeout_is_stopped_at_original_due_without_task_id_or_retry(self):
        self.wire.create_mode='timeout';self.assertEqual(self.create()['state'],'creation_unknown')
        self.wire.now=self.at+timedelta(minutes=30)
        reopened=SmartControlLedger(self.path);s=self.service(ledger=reopened)
        result=s.run_once()['results']['flow']
        self.assertEqual(result['state'],'stop_accepted_pending_readback')
        self.assertEqual(self.stops(),[(STOP_PATH,{'advertiser_id':9990001,'ad_id':8012367229158357,'opt_type':'DISABLE'})])
        self.assertTrue(result['parent_hold_active']);s.run_once();self.assertEqual(len(self.stops()),1)

    def test_running_worker_stops_at_deadline_without_manual_step(self):
        self.reserve();stopped=threading.Event()
        service=SmartControlService(self.ledger,lambda:copy.deepcopy(self.cfg),self.delegation,
            lambda record:'PRIVATE_TOKEN',enable_network=True,clock=lambda:self.wire.now,interval=1)
        self.services.append(service);self.assertTrue(service.start()['thread_alive'])
        self.create(service)
        self.wire.hook=lambda path:stopped.set() if path==STOP_PATH else None
        self.wire.now=self.at+timedelta(minutes=30)
        self.assertTrue(stopped.wait(4));service.stop()
        self.assertEqual(len(self.stops()),1);self.assertEqual(self.ledger.get('flow')['state'],'stop_accepted_pending_readback')
        self.assertGreaterEqual(service.status()['cycles'],2)

    def test_crash_after_creation_claim_survives_reopen_and_uses_parent_stop(self):
        self.wire.create_mode='crash'
        with self.assertRaises(KeyboardInterrupt):self.create()
        self.assertEqual(self.ledger.get('flow')['state'],'creation_claimed')
        self.wire.now=self.at+timedelta(minutes=30);self.service(ledger=SmartControlLedger(self.path)).run_once()
        self.assertEqual(len(self.stops()),1)

    def test_stop_timeout_and_rejection_hold_parent_and_never_retry(self):
        for mode in ('timeout','rejected','ambiguous','no_request_id'):
            with self.subTest(mode=mode):
                # Use a separate parent and record so no earlier hold is bypassed.
                parent=8012367229158357+len(self.wire.calls)
                body='{"advertiser_id":9990001,"ad_id":'+str(parent)+',"budget":100.00}'
                run_id='r'+str(parent);self.ledger.reserve(run_id,body,30,self.wire.now)
                self.ledger.claim_creation(run_id,body,self.wire.now)
                self.wire.now+=timedelta(minutes=30);self.wire.stop_mode=mode
                s=self.service();result=s.step(run_id);count=len(self.stops());s.step(run_id)
                self.assertTrue(result['parent_hold_active']);self.assertEqual(len(self.stops()),count)
                self.assertFalse(self.ledger.get(run_id)['platform_state_observed'])

    def test_stop_crash_keeps_claim_without_replaying_after_restart(self):
        self.create();self.wire.now=self.at+timedelta(minutes=30);self.wire.stop_mode='crash'
        with self.assertRaises(KeyboardInterrupt):self.service().step('flow')
        self.assertEqual(self.ledger.get('flow')['state'],'stop_claimed')
        result=self.service(ledger=SmartControlLedger(self.path)).step('flow')
        self.assertEqual(result['state'],'needs_independent_review');self.assertEqual(len(self.stops()),1)

    def test_known_native_creation_rejection_releases_unused_hold_and_cancels_stop(self):
        self.wire.create_mode='rejected';out=self.create()
        self.assertEqual(out['state'],'creation_rejected');self.assertFalse(out['parent_hold_active'])
        self.wire.now+=timedelta(minutes=30);self.service().run_once();self.assertEqual(self.stops(),[])
        AttemptLedger(self.path).claim('other','digest',self.wire.now,{'scope':'plan','account_id':'9990001','entity_id':'8012367229158357'})

    def test_untrusted_creation_rejection_does_not_release_or_cancel_deadline(self):
        self.wire.create_mode='rejected';self.wire.clock_mode='stale';out=self.create()
        self.assertEqual(out['state'],'creation_unknown');self.assertTrue(out['parent_hold_active'])
        self.assertFalse(self.ledger.get('flow')['creation_receipt']['native_origin_verified'])

    def test_no_delegate_no_stop_scope_or_expired_delegation_prevents_credential_read(self):
        self.create();self.wire.now=self.at+timedelta(minutes=30);provider=Mock(return_value='PRIVATE_TOKEN');s=self.service(token_provider=provider)
        for change in ('missing','stop','account','expired'):
            self.delegate_available=True;self.allow_stop=True;self.cfg['accounts'][0]['observe']=True
            if change=='missing':self.delegate_available=False
            if change=='stop':self.allow_stop=False
            if change=='account':self.cfg['accounts'][0]['observe']=False
            if change=='expired':self.wire.now=self.at+timedelta(hours=3)
            with self.subTest(change=change),self.assertRaises(PermissionError):s.step('flow')
        provider.assert_not_called();self.assertEqual(self.stops(),[])

    def test_stop_delegation_retracted_during_token_lookup_is_rechecked_before_io(self):
        self.create();self.wire.now=self.at+timedelta(minutes=30)
        def provider(record):self.allow_stop=False;return 'PRIVATE_TOKEN'
        result=self.service(token_provider=provider).run_once()
        self.assertEqual(result['problems'],{'flow':'authorization_scope_or_stop_not_ready'})
        self.assertEqual(self.stops(),[]);self.assertIsNone(self.ledger.get('flow')['stop_sent_at'])

    def test_clock_rollback_after_restart_or_between_worker_cycles_is_reported(self):
        self.create();self.wire.now=self.at-timedelta(seconds=1);s=self.service(ledger=SmartControlLedger(self.path));provider=Mock()
        s.token_provider=provider
        with self.assertRaises(ValueError):s.step('flow')
        provider.assert_not_called();self.assertEqual(self.stops(),[])
        self.wire.now=self.at;s.run_once();self.wire.now=self.at-timedelta(seconds=1)
        with self.assertRaises(ValueError):s.run_once()

    def test_delegation_binds_exact_parent_body_duration_and_create_permission(self):
        self.reserve();provider=Mock();s=self.service(start=True);original=self.delegation
        for changes in ({'parent_plan_id':'8250068783313802'},{'duration_minutes':45},
            {'creation_request_digest':'a'*64},{'allow_create':False}):
            s.delegation_provider=lambda record,changes=changes:replace(original(record),**changes)
            with self.subTest(changes=changes),self.assertRaises(PermissionError):self.create(s,provider)
        provider.assert_not_called();self.assertEqual(self.wire.calls,[])

    def test_before_due_and_unsent_reservations_do_not_read_credentials_or_invent_deadline(self):
        self.reserve();provider=Mock();s=self.service(token_provider=provider)
        self.assertEqual(s.step('flow')['state'],'not_due');self.assertIsNone(self.ledger.get('flow')['due_at'])
        provider.assert_not_called();self.assertEqual(self.wire.calls,[])
        self.ledger.claim_creation('flow',self.body,self.at)
        self.assertEqual(s.step('flow')['state'],'not_due');provider.assert_not_called()

    def test_shared_parent_guard_blocks_plan_chase_boost_and_creative_reservations(self):
        self.reserve();at=self.at;identity={'scope':'plan','account_id':'9990001','entity_id':'8012367229158357'}
        with self.assertRaises(ValueError):AttemptLedger(self.path).claim('other','x',at,identity)
        deadlines=DeadlineLedger(self.path)
        with self.assertRaises(ValueError):deadlines.reserve('boost','9990001','8012367229158357','one_click_boost',None,1,'a'*64,at)
        from monitor.plan_locks import check_reservations
        with self.ledger.connect() as db,self.assertRaises(ValueError):check_reservations(db,('9990001','8012367229158357'))
        with self.assertRaises(ValueError):self.reserve('duplicate')
        # Account identity remains separate even when a parent numeric ID repeats.
        self.ledger.reserve('different_account',self.body.replace('9990001','9990002'),30,at)

    def test_existing_other_task_or_submission_hold_blocks_smart_reservation(self):
        attempts=AttemptLedger(self.path)
        attempts.claim('unknown','x',self.at,{'scope':'plan','account_id':'9990001','entity_id':'8012367229158357'})
        with self.assertRaises(ValueError):self.reserve()
        attempts.finish('unknown','failed',{})
        DeadlineLedger(self.path).reserve('boost','9990001','8012367229158357','one_click_boost',None,1,'a'*64,self.at)
        with self.assertRaises(ValueError):self.reserve()

    def test_cancel_is_only_for_unsent_and_cannot_release_after_possible_io(self):
        self.reserve();self.ledger.cancel_before_creation('flow',self.at)
        with self.assertRaises(ValueError):self.ledger.claim_creation('flow',self.body,self.at)
        self.reserve('second');self.ledger.claim_creation('second',self.body,self.at)
        with self.assertRaises(ValueError):self.ledger.cancel_before_creation('second',self.at)

    def test_credentials_callback_changes_to_target_budget_or_forecast_are_rechecked(self):
        for change in ('target','budget','forecast'):
            with self.subTest(change=change):
                self.reserve();s=self.service(start=True)
                def provider():
                    if change=='target':self.cfg['accounts'][0]['target_roi']=None
                    if change=='budget':self.rows[-2]['context']['smart_control_budget']=101
                    if change=='forecast':self.wire.now+=timedelta(minutes=2)
                    return 'PRIVATE_TOKEN'
                with self.assertRaises((PermissionError,ValueError)):self.create(s,provider)
                self.assertEqual(self.wire.calls,[]);self.assertIsNone(self.ledger.get('flow')['due_at'])
                s.stop();self.ledger.cancel_before_creation('flow',self.wire.now)
                # Next case gets a fresh isolated database and baseline.
                if change!='forecast':
                    self.path=Path(self.temp.name)/(change+'.db');self.ledger=SmartControlLedger(self.path)
                    self.rows,self.cfg,self.at,self.proposal,self.evaluation=fixture();self.cfg['accounts'][0]['observe']=True

    def test_service_stop_and_wrong_duration_are_rechecked_before_create_io(self):
        self.reserve(minutes=45);s=self.service(start=True);provider=Mock()
        with self.assertRaises(ValueError):self.create(s,provider)
        provider.assert_not_called();s.stop()
        with self.assertRaises(PermissionError):s.require_ready('flow',self.body,self.at)

    def test_new_parent_snapshot_appended_during_credentials_is_not_hidden_by_list_copy(self):
        self.reserve();s=self.service(start=True)
        def provider():
            row=copy.deepcopy(self.rows[-2]);row['id']='new-current-parent'
            row['source']['data_at']=(self.at+timedelta(seconds=1)).isoformat()
            row['source']['captured_at']=row['source']['data_at']
            row['context']['smart_control_budget']=101
            self.rows.append(row)
            return 'PRIVATE_TOKEN'
        with self.assertRaises((PermissionError,ValueError)):self.create(s,provider)
        self.assertEqual(self.wire.calls,[]);self.assertIsNone(self.ledger.get('flow')['due_at'])

    def test_generator_inputs_survive_all_current_evaluations(self):
        self.reserve();s=self.service(start=True)
        client=SmartControlClient(self.ledger,s,enable_network=True,clock=lambda:self.wire.now)
        result=client.dispatch('flow',self.proposal['id'],(r for r in self.rows),self.cfg,lambda:'PRIVATE_TOKEN',
            events=(r for r in []),migrations=(r for r in []))
        self.assertEqual(result['state'],'creation_accepted_pending_readback');self.assertEqual(len(self.wire.calls),1)

    def test_parallel_creators_make_one_request_and_losing_caller_does_not_cancel_deadline(self):
        self.reserve();s=self.service(start=True);barrier=threading.Barrier(2)
        def provider():barrier.wait(timeout=5);return 'PRIVATE_TOKEN'
        def dispatch():
            try:return self.create(s,provider)['state']
            except (PermissionError,ValueError):return 'blocked'
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:dispatch(),range(2)))
        self.assertEqual(results.count('creation_accepted_pending_readback'),1)
        self.assertEqual(len(self.wire.calls),1);self.assertTrue(self.ledger.get('flow')['parent_hold_active'])

    def test_parallel_stoppers_send_once_and_loser_cannot_overwrite_pending_receipt(self):
        self.create();self.wire.now=self.at+timedelta(minutes=30);barrier=threading.Barrier(2);release=threading.Event()
        self.addCleanup(release.set)
        def provider(record):barrier.wait(timeout=5);return 'PRIVATE_TOKEN'
        def hook(path):
            if path==STOP_PATH:release.wait(timeout=5)
        self.wire.hook=hook;s=self.service(token_provider=provider)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(s.step,'flow') for _ in range(2)]
            done,pending=wait(futures,timeout=4,return_when=FIRST_COMPLETED)
            self.assertEqual(len(done),1);self.assertEqual(self.ledger.get('flow')['state'],'stop_claimed')
            release.set();results=[f.result(timeout=5) for f in futures]
        self.assertEqual(len(self.stops()),1);self.assertEqual(self.ledger.get('flow')['state'],'stop_accepted_pending_readback')

    def test_late_create_reply_cannot_rewind_stop_or_extend_deadline(self):
        self.reserve();self.ledger.claim_creation('flow',self.body,self.at)
        self.wire.now=self.at+timedelta(minutes=30);self.service().step('flow')
        before=self.ledger.get('flow');self.ledger.finish_creation('flow',None,self.wire.now)
        after=self.ledger.get('flow');self.assertEqual(after['state'],before['state']);self.assertEqual(after['due_at'],before['due_at'])

    def test_secrets_raw_errors_and_response_messages_are_not_persisted(self):
        self.create();self.wire.now=self.at+timedelta(minutes=30);self.wire.stop_mode='timeout';self.service().step('flow')
        with self.ledger.connect() as db:
            payload=str(db.execute('SELECT data_json FROM smart_control_runs').fetchall())+str(db.execute('SELECT details_json FROM smart_control_journal').fetchall())
        for secret in ('PRIVATE_TOKEN','PRIVATE_MESSAGE','PRIVATE_AUTHORIZATION','PRIVATE_TOKEN_AND_ERROR'):
            self.assertNotIn(secret,payload)

    def test_invalid_native_fields_duration_ids_or_stale_reservation_never_send(self):
        for body in (self.body.replace('100.00','0.00'),self.body.replace('100.00','true'),
            self.body[:-1]+',"duration":1}',self.body.replace('9990001','09990001')):
            with self.subTest(body=body),self.assertRaises(ValueError):self.ledger.reserve('x',body,30,self.at)
        for minutes in (True,29,61,30.5):
            with self.assertRaises(ValueError):self.ledger.reserve('x',self.body,minutes,self.at)
        self.reserve();self.wire.now+=timedelta(seconds=61);s=self.service(start=True);provider=Mock()
        with self.assertRaises((ValueError,PermissionError)):self.create(s,provider)
        provider.assert_not_called();self.assertEqual(self.wire.calls,[])


if __name__=='__main__':unittest.main()
