import copy
import json
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock,patch

from monitor.control_creation_client import ControlCreationPermit
from monitor.control_deadline import DeadlineLedger
from monitor.deadline_service import DeadlineService,DeadlineDelegation,require_live_lease
from monitor.evaluation import evaluate_all
from monitor.material_chase_creation import preview_material_chase
from monitor.material_chase_client import MaterialChaseClient,MaterialChasePermit
from monitor.material_chase_demo import chase_creation_scenario
from monitor.native_control_io import CREATE_PATH,STATUS_PATH
from monitor.submission import AttemptLedger
from test_control_deadline import Wire,TASK


class MaterialChaseClientTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'native.db';self.attempts=AttemptLedger(self.path);self.deadlines=DeadlineLedger(self.path)
        self.rows,self.cfg,self.at,self.payload=chase_creation_scenario(json.loads(Path('config.json').read_text('utf-8')))
        for row in self.rows:row['source']['kind']='import'
        self.evaluation=evaluate_all(self.rows,self.cfg,self.at)
        self.payload['proposal_id']=next(p['id'] for p in self.evaluation['recommendations'] if p['action']=='create_chase' and p['eligible'])
        self.draft=preview_material_chase(self.payload,self.rows,self.cfg,self.evaluation,self.at)['draft']
        self.wire=Wire(self.at);self.allow_end=True;self.delegate_available=True;self.service=None
        http=patch('monitor.native_control_io.urllib.request.build_opener',side_effect=self.wire.opener);http.start();self.addCleanup(http.stop)
        self.addCleanup(lambda:self.service.stop() if self.service else None)

    def delegation(self,record):
        if not self.delegate_available:return None
        return DeadlineDelegation(record['id'],record['account_id'],record['creation_request_digest'],
            self.at,self.at+timedelta(hours=6),'PRIVATE_STOP_AUTHORIZATION',self.allow_end)

    def reserve(self):
        return self.deadlines.reserve('deadline','9990001','8012367229158357','material_chase','8753264005940210',2,
            self.draft['body_digest'],self.wire.now)

    def start(self,interval=30):
        self.service=DeadlineService(self.deadlines,lambda:copy.deepcopy(self.cfg),self.delegation,
            lambda job:'PRIVATE_TOKEN',enable_network=True,clock=lambda:self.wire.now,interval=interval)
        self.assertTrue(self.service.start()['thread_alive']);return self.service

    def ready(self):
        self.reserve();self.start();return self.service.lease('deadline',self.draft['body_digest'])

    def permit(self):
        return MaterialChasePermit('9990001',self.draft['body_digest'],self.draft['qualification_digest'],'deadline',
            self.at,self.at+timedelta(minutes=2),'PRIVATE_CREATE_AUTHORIZATION')

    def dispatch(self,lease=None,permit=None,provider=None,attempt='creation',client=None,**kwargs):
        client=client or MaterialChaseClient(self.attempts,enable_network=True,clock=lambda:self.wire.now)
        return client.dispatch(attempt,self.payload,self.rows,self.cfg,permit or self.permit(),lease,
            provider or (lambda:'PRIVATE_TOKEN'),**kwargs)

    def creations(self):return [r for r in self.wire.calls if r[1]==CREATE_PATH]
    def ends(self):return [r for r in self.wire.calls if r[1]==STATUS_PATH]

    def test_default_off_creation_and_service_touch_no_clock_inputs_ledger_or_credentials(self):
        clock=Mock(side_effect=AssertionError());ledger=Mock();provider=Mock()
        with self.assertRaises(PermissionError):MaterialChaseClient(ledger,clock=clock).dispatch('x',None,None,None,None,None,provider)
        service=DeadlineService(ledger,None,None,None,clock=clock)
        with self.assertRaises(PermissionError):service.start()
        with self.assertRaises(PermissionError):service.run_once()
        with self.assertRaises(PermissionError):service.lease('x','y')
        clock.assert_not_called();provider.assert_not_called();self.assertEqual(ledger.mock_calls,[])

    def test_active_worker_lease_and_exact_permit_bind_one_native_create_to_its_deadline(self):
        lease=self.ready();out=self.dispatch(lease)
        self.assertTrue(out['business_accepted']);self.assertEqual(out['created_task_id'],TASK)
        self.assertTrue(out['deadline_binding_completed']);self.assertTrue(out['creation_receipt']['origin_verified'])
        job=self.deadlines.get('deadline');self.assertEqual(job['control_type'],'material_chase');self.assertEqual(job['task_id'],TASK)
        self.assertEqual(job['due_at'],(self.at+timedelta(hours=2)).isoformat())
        with self.attempts.connect() as db:state,ctx=db.execute('SELECT state,context_json FROM submission_attempts').fetchone()
        self.assertEqual(state,'unknown');ctx=json.loads(ctx);self.assertIsNone(ctx['entity_id']);self.assertEqual(ctx['material_id'],'8753264005940210')
        self.assertEqual(ctx['parent_plan_id'],'8012367229158357');self.assertEqual(len(self.creations()),1)

    def test_appended_new_creative_after_token_lookup_is_seen_by_current_qualification(self):
        lease=self.ready()
        def provider():
            row=copy.deepcopy(next(r for r in reversed(self.rows) if r['scope']=='creative'))
            row['id']='appended-current-creative'
            row['source']['data_at']=(self.at+timedelta(seconds=1)).isoformat()
            row['source']['captured_at']=row['source']['data_at']
            row['control']['running']=False
            self.rows.append(row);return 'PRIVATE_TOKEN'
        with self.assertRaises((ValueError,PermissionError)):self.dispatch(lease,provider=provider)
        self.assertEqual(self.creations(),[])
        with self.attempts.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM submission_attempts').fetchone()[0],0)

    def test_configuration_dict_cannot_replace_private_running_worker_lease(self):
        self.reserve();provider=Mock()
        with self.assertRaises(PermissionError):self.dispatch({'enabled':True},provider=provider)
        provider.assert_not_called();self.assertEqual(self.wire.calls,[])

    def test_manual_tick_without_a_live_thread_cannot_issue_a_creation_lease(self):
        self.reserve();service=DeadlineService(self.deadlines,lambda:self.cfg,self.delegation,lambda job:'PRIVATE_TOKEN',
            enable_network=True,clock=lambda:self.wire.now)
        service.run_once()
        with self.assertRaises(PermissionError):service.lease('deadline',self.draft['body_digest'])

    def test_stopped_worker_revokes_lease_before_reading_creation_credentials(self):
        lease=self.ready();self.service.stop();provider=Mock()
        with self.assertRaises(PermissionError):self.dispatch(lease,provider=provider)
        provider.assert_not_called();self.assertEqual(self.wire.calls,[])

    def test_expired_wall_or_monotonic_lease_never_reads_credentials(self):
        lease=self.ready();provider=Mock()
        for bad in (replace(lease,expires_at=self.at),replace(lease,_issued_monotonic=lease._issued_monotonic-31)):
            with self.assertRaises(PermissionError):self.dispatch(bad,provider=provider)
        provider.assert_not_called();self.assertEqual(self.wire.calls,[])

    def test_stale_worker_cycle_and_unavailable_delegation_block_creation(self):
        lease=self.ready();provider=Mock();self.delegate_available=False
        with self.assertRaises(PermissionError):self.dispatch(lease,provider=provider)
        self.delegate_available=True
        with self.service._status_lock:self.service._last_tick=self.at-timedelta(minutes=2)
        with self.assertRaises(PermissionError):self.dispatch(lease,provider=provider)
        provider.assert_not_called();self.assertEqual(self.wire.calls,[])

    def test_read_only_stop_policy_cannot_authorize_long_lived_creation(self):
        self.reserve();self.allow_end=False;self.start()
        with self.assertRaises(PermissionError):self.service.lease('deadline',self.draft['body_digest'])
        self.assertEqual(self.wire.calls,[])

    def test_permit_types_account_body_qualification_and_deadline_are_not_interchangeable(self):
        lease=self.ready();provider=Mock();permit=self.permit()
        wrong=[replace(permit,account_id='other'),replace(permit,request_digest='bad'),replace(permit,qualification_digest='bad'),
            replace(permit,deadline_id='other'),ControlCreationPermit('9990001',permit.request_digest,self.at,self.at+timedelta(minutes=2),'r')]
        for value in wrong:
            with self.assertRaises(PermissionError):self.dispatch(lease,value,provider)
        provider.assert_not_called();self.assertEqual(self.wire.calls,[])

    def test_changed_assessment_that_keeps_body_identical_still_invalidates_reviewed_qualification(self):
        lease=self.ready();self.cfg['accounts'][0]['target_roi']=2.000001;provider=Mock()
        with self.assertRaises(PermissionError):self.dispatch(lease,provider=provider)
        provider.assert_not_called();self.assertEqual(self.wire.calls,[])

    def test_token_callback_stopping_worker_or_changing_scope_is_rechecked(self):
        lease=self.ready()
        def token():self.service.stop();return 'PRIVATE_TOKEN'
        with self.assertRaises(PermissionError):self.dispatch(lease,provider=token)
        self.assertEqual(self.creations(),[])
        with self.attempts.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM submission_attempts').fetchone()[0],0)

    def test_after_sqlite_claim_scope_change_prevents_creation_io(self):
        lease=self.ready();claim=self.attempts.claim
        def mutate(*args,**kwargs):claim(*args,**kwargs);self.cfg['accounts'][0]['observe']=False
        with patch.object(self.attempts,'claim',side_effect=mutate):out=self.dispatch(lease)
        self.assertEqual(out['state'],'failed_before_io');self.assertFalse(out['submitted']);self.assertEqual(self.creations(),[])
        self.assertTrue(self.deadlines.get('deadline')['parent_hold_active'])

    def test_snapshot_generator_survives_three_full_evaluations(self):
        lease=self.ready();client=MaterialChaseClient(self.attempts,enable_network=True,clock=lambda:self.wire.now)
        out=client.dispatch('creation',self.payload,(r for r in self.rows),self.cfg,self.permit(),lease,lambda:'PRIVATE_TOKEN',events=(r for r in []))
        self.assertTrue(out['business_accepted']);self.assertEqual(len(self.creations()),1)

    def test_unknown_create_or_missing_native_origin_does_not_retry_or_guess_an_id(self):
        lease=self.ready();self.wire.creation_mode='timeout';out=self.dispatch(lease)
        self.assertEqual(out['state'],'unknown');self.assertTrue(out['parent_hold_active'])
        with self.assertRaises(ValueError):self.dispatch(lease,attempt='retry')
        self.assertEqual(len(self.creations()),1)

    def test_bad_clock_origin_retains_accepted_id_without_authorizing_deadline_io(self):
        lease=self.ready();self.wire.date_mode='missing';out=self.dispatch(lease)
        self.assertEqual(out['created_task_id'],TASK);self.assertFalse(out['creation_receipt']['origin_verified'])
        self.assertFalse(out['deadline_binding_completed']);self.wire.now+=timedelta(hours=2)
        result=self.service.run_once();self.assertEqual(result['problems']['deadline'],'creation_identity_unknown');self.assertEqual(self.ends(),[])

    def test_local_binding_failure_preserves_known_native_creation_id_and_hold(self):
        lease=self.ready()
        with patch.object(DeadlineLedger,'bind_creation',side_effect=RuntimeError('PRIVATE_TOKEN')):out=self.dispatch(lease)
        self.assertEqual(out['created_task_id'],TASK);self.assertTrue(out['creation_receipt']['origin_verified']);self.assertFalse(out['deadline_binding_completed'])
        self.assertTrue(out['parent_hold_active']);self.assertEqual(len(self.creations()),1)

    def test_pre_claimed_creation_after_process_exit_cannot_be_sent_again(self):
        lease=self.ready()
        with patch('monitor.material_chase_client.create_task',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.dispatch(lease)
        with self.attempts.connect() as db:self.assertEqual(db.execute('SELECT state FROM submission_attempts').fetchone()[0],'claimed')
        with self.assertRaises(ValueError):self.dispatch(lease,attempt='another')
        self.assertEqual(self.creations(),[])

    def test_worker_reopen_rebuilds_authorization_and_observes_two_terminal_reads_after_single_end(self):
        lease=self.ready();self.dispatch(lease);self.service.stop();self.deadlines=DeadlineLedger(self.path)
        self.wire.now+=timedelta(hours=2);self.start()
        self.assertEqual(len(self.ends()),1);self.assertEqual(self.deadlines.get('deadline')['state'],'end_accepted_pending_readback')
        self.wire.status='DISABLE';self.wire.now+=timedelta(seconds=120);self.service.run_once()
        self.wire.now+=timedelta(seconds=120);self.service.run_once()
        self.assertEqual(self.deadlines.get('deadline')['state'],'closed_observed')
        with self.attempts.connect() as db:self.assertEqual(db.execute('SELECT state FROM submission_attempts').fetchone()[0],'reconciled_terminal')
        self.assertEqual(len(self.creations()),1);self.assertEqual(len(self.ends()),1)

    def test_scheduler_failed_or_unknown_end_never_automatically_reposts(self):
        lease=self.ready();self.dispatch(lease);self.wire.end_mode='timeout';self.wire.now+=timedelta(hours=2)
        self.service.run_once();self.service.run_once();self.service.run_once()
        self.assertEqual(len(self.ends()),1);self.assertEqual(self.deadlines.get('deadline')['state'],'end_unknown')

    def test_restored_worker_without_delegation_keeps_overdue_hold_without_api_calls(self):
        lease=self.ready();self.dispatch(lease);self.service.stop();self.delegate_available=False;self.wire.now+=timedelta(hours=2)
        self.start();self.assertIn('deadline',self.service.status()['problems']);self.assertEqual(self.ends(),[])
        self.assertTrue(self.deadlines.get('deadline')['parent_hold_active'])

    def test_worker_status_and_db_never_persist_tokens_or_authorization_text(self):
        lease=self.ready();self.dispatch(lease);self.wire.now+=timedelta(hours=2);self.service.run_once()
        with self.deadlines.connect() as db:
            values=str(db.execute('SELECT * FROM control_deadlines').fetchall())+str(db.execute('SELECT * FROM control_deadline_journal').fetchall())+str(db.execute('SELECT * FROM submission_attempts').fetchall())
        values+=json.dumps(self.service.status())
        for value in ('PRIVATE_TOKEN','PRIVATE_STOP_AUTHORIZATION','PRIVATE_CREATE_AUTHORIZATION','PRIVATE_MESSAGE'):self.assertNotIn(value,values)

    def test_broker_revocation_during_token_lookup_is_rechecked_before_claim(self):
        lease=self.ready()
        def token():self.delegate_available=False;return 'PRIVATE_TOKEN'
        with self.assertRaises(PermissionError):self.dispatch(lease,provider=token)
        self.assertEqual(self.creations(),[])

    def test_worker_stopping_after_claim_prevents_io_and_keeps_cancelable_unsent_reservation(self):
        lease=self.ready();claim=self.attempts.claim
        def stop_after_claim(*args,**kwargs):claim(*args,**kwargs);self.service.stop()
        with patch.object(self.attempts,'claim',side_effect=stop_after_claim):out=self.dispatch(lease)
        self.assertEqual(out['state'],'failed_before_io');self.assertEqual(self.creations(),[])
        self.assertEqual(self.deadlines.cancel_before_creation('deadline',self.wire.now)['state'],'cancelled_before_creation')

    def test_future_end_authorization_window_and_failed_worker_cycle_prevent_lease(self):
        self.reserve();self.start();record=self.deadlines.get('deadline');short=replace(self.delegation(record),expires_at=self.at+timedelta(hours=1))
        with patch.object(self.service,'delegation_provider',return_value=short):
            with self.assertRaises(PermissionError):self.service.lease('deadline',self.draft['body_digest'])
        with self.service._status_lock:self.service._problems={'service':'cycle_not_complete'}
        with self.assertRaises(PermissionError):self.service.lease('deadline',self.draft['body_digest'])

    def test_private_lease_cannot_be_used_with_another_native_ledger(self):
        lease=self.ready();other=AttemptLedger(Path(self.temp.name)/'other.db');provider=Mock()
        with self.assertRaises(PermissionError):self.dispatch(lease,provider=provider,client=MaterialChaseClient(other,enable_network=True,clock=lambda:self.wire.now))
        provider.assert_not_called();self.assertEqual(self.creations(),[])

    def test_active_worker_can_observe_with_read_only_delegation_without_posting_end(self):
        lease=self.ready();self.dispatch(lease);self.allow_end=False;self.wire.now+=timedelta(hours=2)
        result=self.service.run_once();self.assertTrue(result['results']['deadline']['read_attempted'])
        self.assertEqual(self.ends(),[]);self.assertEqual(self.deadlines.get('deadline')['state'],'watching')

    def test_multi_account_worker_keeps_exact_account_and_parent_boundaries(self):
        lease=self.ready();self.dispatch(lease)
        second=copy.deepcopy(self.rows);payload=copy.deepcopy(self.payload)
        for row in second:
            row['id']='second-'+row['id'];row['account_id']='9990002'
            if row['scope']=='plan':row['entity_id']='2002'
            elif row['scope']=='creative':
                row['entity_id']='3002';row['context'].update(parent_plan_id='2002',plan_id='2002',material_id='3002')
            else:row['entity_id']='9990002'
        self.rows+=second;self.cfg['accounts'].append({**self.cfg['accounts'][0],'id':'9990002'})
        payload.update(account_id='9990002',parent_plan_id='2002',creative_snapshot_id='second-'+payload['creative_snapshot_id'],
            parent_snapshot_id='second-'+payload['parent_snapshot_id'])
        payload['material'].update(material_id='3002',snapshot_id=payload['creative_snapshot_id'])
        payload['targeting_review']['parent_snapshot_id']=payload['parent_snapshot_id']
        evaluation=evaluate_all(self.rows,self.cfg,self.at)
        payload['proposal_id']=next(p['id'] for p in evaluation['recommendations'] if p['action']=='create_chase' and p['account_id']=='9990002' and p['eligible'])
        draft=preview_material_chase(payload,self.rows,self.cfg,evaluation,self.at)['draft'];self.assertIsNotNone(draft)
        self.deadlines.reserve('deadline2','9990002','2002','material_chase','3002',2,draft['body_digest'],self.at)
        permit=MaterialChasePermit('9990002',draft['body_digest'],draft['qualification_digest'],'deadline2',self.at,self.at+timedelta(minutes=2),'second-create')
        result=MaterialChaseClient(self.attempts,enable_network=True,clock=lambda:self.wire.now).dispatch(
            'second',payload,self.rows,self.cfg,permit,self.service.lease('deadline2',draft['body_digest']),lambda:'PRIVATE_TOKEN')
        self.assertTrue(result['business_accepted']);self.wire.now+=timedelta(hours=2);self.service.run_once()
        self.assertEqual({r[2]['advertiser_id'] for r in self.ends()},{9990001,9990002})
        self.assertEqual(len(self.ends()),2)

    def test_broker_callback_exceptions_are_redacted_at_the_lease_boundary(self):
        self.ready()
        for attribute in ('delegation_provider','settings_provider'):
            with patch.object(self.service,attribute,side_effect=RuntimeError('PRIVATE_TOKEN')):
                with self.assertRaises(PermissionError) as error:self.service.lease('deadline',self.draft['body_digest'])
                self.assertNotIn('PRIVATE_TOKEN',str(error.exception))
        self.assertEqual(self.wire.calls,[])

    def test_real_worker_loop_wakes_for_a_due_job_without_manual_ticks(self):
        lease=self.ready();self.dispatch(lease);self.service.stop();self.start(interval=1)
        self.wire.now+=timedelta(hours=2);until=time.monotonic()+3
        while time.monotonic()<until:
            if self.deadlines.get('deadline')['state']=='end_accepted_pending_readback':break
            time.sleep(.01)
        self.assertEqual(self.deadlines.get('deadline')['state'],'end_accepted_pending_readback')
        self.assertEqual(len(self.ends()),1)


if __name__=='__main__':unittest.main()
