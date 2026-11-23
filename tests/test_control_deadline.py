import copy
import json
import tempfile
import threading
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta,timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import Mock,patch

from monitor.control_creation_client import ControlCreationClient,ControlCreationPermit
from monitor.control_deadline import DeadlineLedger,DeadlineClient,DeadlinePermit,deadline_preview
from monitor.native_control_io import create_task,digest,CREATE_PATH,STATUS_PATH
from monitor.submission import AttemptLedger
from test_control_creation import fixture

TASK='8753264005940210'


class Wire:
    """Fixed HTTPS boundary mock. No sockets or real platform credentials."""
    def __init__(self,now):
        self.now=now;self.calls=[];self.status='PROCESSING';self.date_mode='ok';self.rid_mode='fresh'
        self.end_mode='accepted';self.creation_mode='accepted';self.hook=None;self.barrier=None;self.lock=threading.Lock()

    def opener(self,*handlers):
        wire=self
        class Opener:
            def open(self,request,timeout):
                parsed=urllib.parse.urlsplit(request.full_url);body=json.loads(request.data) if request.data else None
                if parsed.netloc!='api.oceanengine.com':raise AssertionError('unexpected host')
                with wire.lock:
                    wire.calls.append((request.method,parsed.path,body));count=len(wire.calls)
                if parsed.path==STATUS_PATH:
                    if wire.end_mode=='timeout':raise TimeoutError('PRIVATE_TOKEN')
                    if wire.end_mode=='crash':raise KeyboardInterrupt()
                    payload={'code':0,'data':{'task_ids':[int(TASK)],'errors':[]}}
                    if wire.end_mode=='rejected':payload={'code':0,'data':{'task_ids':[],'errors':[{'task_id':int(TASK),'error_message':'PRIVATE_ERROR'}]}}
                    if wire.end_mode=='ambiguous':payload={'code':0,'data':{}}
                elif parsed.path==CREATE_PATH:
                    if wire.creation_mode=='timeout':raise TimeoutError('PRIVATE_TOKEN')
                    payload={'code':0,'data':{'id':int(TASK)}} if wire.creation_mode=='accepted' else {'code':1,'data':{}}
                else:
                    params=urllib.parse.parse_qs(parsed.query)
                    if json.loads(params['filtering'][0]).get('having_cost')!='ALL':raise AssertionError('zero-cost filter missing')
                    row={'id':int(TASK),'task_status':wire.status}
                    payload={'code':0,'data':{'task_list':[row],'page_info':{'page':1,'page_size':10,'total_number':1,'total_page':1}}}
                    if wire.barrier:wire.barrier.wait(timeout=5)
                    if wire.hook:wire.hook()
                if wire.rid_mode!='missing':payload['request_id']='same-rid' if wire.rid_mode=='same' else 'rid-'+str(count)
                payload['message']='PRIVATE_MESSAGE'
                date=wire.now-timedelta(hours=1) if wire.date_mode=='stale' else wire.now
                headers={} if wire.date_mode=='missing' else {'Date':format_datetime(date.astimezone(timezone.utc),usegmt=True)}
                raw=json.dumps(payload).encode()
                class Response:
                    def __enter__(self):return self
                    def __exit__(self,*args):pass
                    def geturl(self):return request.full_url
                    def read(self,n):return raw[:n]
                result=Response();result.headers=headers;return result
        return Opener()


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'native.db';self.attempts=AttemptLedger(self.path);self.ledger=DeadlineLedger(self.path)
        self.rows,self.cfg,self.at,self.proposal,self.preview=fixture();self.body=self.preview['request']['body_json']
        self.wire=Wire(self.at);self.http=patch('monitor.native_control_io.urllib.request.build_opener',side_effect=self.wire.opener)
        self.http.start();self.addCleanup(self.http.stop)

    def reserve(self,job_id='deadline',**changes):
        values={'account_id':'9990001','parent_plan_id':'8012367229158357','control_type':'one_click_boost',
            'material_id':None,'duration_hours':1,'request_digest':digest(self.body),'now':self.wire.now}
        values.update(changes);return self.ledger.reserve(job_id,**values)

    def create(self):
        self.reserve();permit=ControlCreationPermit('9990001',self.preview['digest'],self.at,self.at+timedelta(minutes=2),'test-only')
        return ControlCreationClient(self.attempts,enable_network=True,clock=lambda:self.wire.now).dispatch(
            'creation',self.proposal['id'],self.rows,self.cfg,permit,lambda:'PRIVATE_TOKEN',deadline_id='deadline')

    def step(self,*,allow=True,client=None,provider=None):
        record=self.ledger.get('deadline');preview=deadline_preview(record,self.cfg,self.wire.now)
        permit=DeadlinePermit('9990001',preview['digest'],self.wire.now,self.wire.now+timedelta(minutes=2),'test-stop',allow)
        client=client or DeadlineClient(self.ledger,enable_network=True,clock=lambda:self.wire.now)
        return client.step('deadline',self.cfg,permit,provider or (lambda:'PRIVATE_TOKEN'))

    def end_calls(self):return [c for c in self.wire.calls if c[1]==STATUS_PATH]

    def test_reservation_precedes_creation_and_survives_reopen_with_exact_deadline(self):
        self.create();record=DeadlineLedger(self.path).get('deadline')
        self.assertEqual(record['state'],'watching');self.assertEqual(record['task_id'],TASK)
        self.assertEqual(record['due_at'],(self.at+timedelta(hours=1)).isoformat())
        self.assertEqual(record['owner_attempt_id'],'creation');self.assertTrue(record['native_creation_verified'])

    def test_default_off_rejects_before_ledger_clock_or_credentials(self):
        clock=Mock(side_effect=AssertionError());ledger=Mock();provider=Mock()
        with self.assertRaises(PermissionError):DeadlineClient(ledger,clock=clock).step('x',{},None,provider)
        clock.assert_not_called();ledger.get.assert_not_called();provider.assert_not_called()

    def test_before_due_does_not_read_credentials_or_native_state(self):
        self.create();provider=Mock();calls=len(self.wire.calls)
        result=self.step(provider=provider)
        self.assertEqual(result['state'],'not_due');provider.assert_not_called();self.assertEqual(len(self.wire.calls),calls)

    def test_reservation_blocks_unrelated_parent_submissions_and_creative_reservation(self):
        self.reserve()
        with self.assertRaises(ValueError):self.attempts.claim('other','x',self.at,{'scope':'plan','account_id':'9990001','entity_id':'8012367229158357'})
        from monitor.creative_workflow import WorkflowLedger
        from monitor.plan_locks import check_reservations
        workflow=WorkflowLedger(self.path)
        with workflow.connect() as db:
            with self.assertRaises(ValueError):check_reservations(db,('9990001','8012367229158357'))
        self.assertEqual(self.wire.calls,[])

    def test_cancelled_or_wrong_digest_deadline_cannot_be_used_by_a_creator(self):
        job=self.reserve();self.ledger.cancel_before_creation('deadline',self.at)
        ctx={'scope':'control_create','action':'one_click_boost','entity_id':None,'account_id':'9990001',
            'parent_plan_id':'8012367229158357','deadline_id':'deadline','request_digest':digest(self.body)}
        with self.assertRaises(ValueError):self.attempts.claim('bad','x',self.at,ctx)
        self.reserve('other');ctx['deadline_id']='other';ctx['request_digest']='a'*64
        with self.assertRaises(ValueError):self.attempts.claim('bad2','x2',self.at,ctx)
        self.assertEqual(self.wire.calls,[])

    def test_bound_creation_cannot_cancel_deadline_or_move_its_identity(self):
        self.create();record=self.ledger.get('deadline')
        with self.assertRaises(ValueError):self.ledger.cancel_before_creation('deadline',self.at)
        with self.assertRaises(ValueError):self.ledger.change(record,'watching',self.at,{'due_at':self.at.isoformat()},event='bad')
        with self.assertRaises(ValueError):self.ledger.change(record,'reserved_before_creation',self.at,event='bad')

    def test_due_uses_single_manual_end_and_not_pause_enable_delete(self):
        self.create();self.wire.now+=timedelta(hours=1)
        result=self.step();self.assertEqual(result['state'],'end_accepted_pending_readback')
        self.assertTrue(result['business_accepted']);self.assertTrue(result['parent_hold_active'])
        self.assertFalse(result['platform_state_observed'])
        self.assertEqual(self.end_calls()[0][2],{'advertiser_id':9990001,'task_ids':[int(TASK)],'opt_type':'DISABLE'})
        self.assertEqual(len(self.end_calls()),1)

    def test_read_only_permission_never_sends_end_even_when_due(self):
        self.create();self.wire.now+=timedelta(hours=1);result=self.step(allow=False)
        self.assertTrue(result['read_attempted']);self.assertFalse(result['end_attempted']);self.assertEqual(self.end_calls(),[])

    def test_two_terminal_reads_close_deadline_and_original_unknown_attempt_across_reopen(self):
        self.create();self.wire.now+=timedelta(hours=1);self.step();self.wire.status='DISABLE'
        self.wire.now+=timedelta(seconds=120);first=self.step();self.assertTrue(first['parent_hold_active'])
        self.ledger=DeadlineLedger(self.path);self.wire.now+=timedelta(seconds=120);result=self.step()
        self.assertEqual(result['state'],'closed_observed');self.assertFalse(result['parent_hold_active'])
        with self.attempts.connect() as db:
            state,res=db.execute("SELECT state,result_json FROM submission_attempts WHERE id='creation'").fetchone()
        self.assertEqual(state,'reconciled_terminal');self.assertFalse(json.loads(res)['parent_hold_active'])
        self.assertEqual(len(self.end_calls()),1)
        identity={'scope':'plan','account_id':'9990001','entity_id':'8012367229158357'}
        with self.assertRaises(ValueError):self.attempts.claim('after','new',self.wire.now,identity)
        with self.assertRaises(ValueError):self.attempts.claim('after','new',self.at+timedelta(hours=2,seconds=-1),identity)
        self.attempts.claim('after','new',self.at+timedelta(hours=2),identity)

    def test_natural_frozen_end_is_observed_without_sending_a_manual_end(self):
        self.create();self.wire.now+=timedelta(hours=1);self.wire.status='FROZEN';self.step()
        self.wire.now+=timedelta(seconds=120);self.assertEqual(self.step()['state'],'closed_observed')
        self.assertEqual(self.end_calls(),[])

    def test_newer_running_or_failed_read_resets_terminal_pair(self):
        self.create();self.wire.now+=timedelta(hours=1);self.wire.status='FROZEN';self.step()
        self.wire.now+=timedelta(seconds=120);self.wire.date_mode='missing';self.step()
        self.assertIsNone(self.ledger.get('deadline')['latest_observation'])
        self.wire.now+=timedelta(seconds=120);self.wire.date_mode='ok';self.assertTrue(self.step()['parent_hold_active'])
        self.wire.now+=timedelta(seconds=120);self.wire.status='PROCESSING';self.step(allow=False)
        self.wire.now+=timedelta(seconds=120);self.wire.status='FROZEN';self.assertTrue(self.step()['parent_hold_active'])
        self.wire.now+=timedelta(seconds=120);self.assertFalse(self.step()['parent_hold_active'])

    def test_repeated_request_id_or_nonterminal_states_do_not_release_hold(self):
        self.create();self.wire.now+=timedelta(hours=1);self.wire.status='FROZEN';self.wire.rid_mode='same';self.step()
        self.wire.now+=timedelta(seconds=120);self.assertTrue(self.step()['parent_hold_active'])
        for state in ('OFFLINE_TIME','OFFLINE_BALANCE','OFFLINE_BUDGET','ROI2_DISABLE','DELETED'):
            self.wire.status=state;self.wire.now+=timedelta(seconds=120);self.assertTrue(self.step()['parent_hold_active'])
        self.assertEqual(self.end_calls(),[])

    def test_unknown_post_is_never_resent_after_reopen(self):
        self.create();self.wire.now+=timedelta(hours=1);self.wire.end_mode='timeout';self.step()
        self.assertEqual(self.ledger.get('deadline')['state'],'end_unknown')
        self.ledger=DeadlineLedger(self.path);self.wire.now+=timedelta(seconds=120);self.step()
        self.assertEqual(len(self.end_calls()),1)
        self.wire.status='DISABLE';self.wire.now+=timedelta(seconds=120);self.step()
        self.wire.now+=timedelta(seconds=120);self.assertFalse(self.step()['parent_hold_active'])

    def test_process_exit_after_claim_keeps_claim_and_next_worker_only_reads(self):
        self.create();self.wire.now+=timedelta(hours=1);self.wire.end_mode='crash'
        with self.assertRaises(KeyboardInterrupt):self.step()
        self.assertEqual(self.ledger.get('deadline')['state'],'end_claimed')
        self.ledger=DeadlineLedger(self.path);self.wire.now+=timedelta(seconds=120);self.step()
        self.assertEqual(len(self.end_calls()),1)

    def test_rejected_or_ambiguous_end_is_not_retried(self):
        self.create();self.wire.now+=timedelta(hours=1);self.wire.end_mode='rejected'
        self.assertEqual(self.step()['state'],'end_rejected');self.wire.now+=timedelta(seconds=120);self.step()
        self.assertEqual(len(self.end_calls()),1)

    def test_concurrent_workers_cannot_send_two_end_requests(self):
        self.create();self.wire.now+=timedelta(hours=1);self.wire.barrier=threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:self.step(),range(2)))
        self.assertEqual(len(self.end_calls()),1);self.assertTrue(any(r.get('business_accepted') for r in results))

    def test_scope_change_during_token_or_read_prevents_end(self):
        self.create();self.wire.now+=timedelta(hours=1)
        def provider():self.cfg['accounts'][0]['observe']=False;return 'PRIVATE_TOKEN'
        self.step(provider=provider);self.assertEqual(self.end_calls(),[])
        self.cfg['accounts'][0]['observe']=True;self.wire.hook=lambda:self.cfg['accounts'][0].update(observe=False)
        self.step();self.assertEqual(self.end_calls(),[])

    def test_wrong_or_expired_permit_rejects_before_credentials(self):
        self.create();self.wire.now+=timedelta(hours=1);provider=Mock()
        permit=DeadlinePermit('9990001','wrong',self.wire.now,self.wire.now+timedelta(minutes=2),'r',True)
        with self.assertRaises(PermissionError):DeadlineClient(self.ledger,enable_network=True,clock=lambda:self.wire.now).step('deadline',self.cfg,permit,provider)
        provider.assert_not_called();self.assertEqual(self.end_calls(),[])

    def test_missing_task_identity_never_guesses_or_reads_credentials(self):
        self.wire.creation_mode='timeout';self.create();provider=Mock();self.wire.now+=timedelta(hours=1)
        result=DeadlineClient(self.ledger,enable_network=True,clock=lambda:self.wire.now).step('deadline',self.cfg,None,provider)
        self.assertEqual(result['state'],'creation_identity_unknown');provider.assert_not_called();self.assertEqual(self.end_calls(),[])

    def test_plain_supplied_creation_dict_cannot_bind_a_native_deadline(self):
        self.reserve();out=self.ledger.bind_creation('deadline',self.body,{'code':0,'data':{'id':int(TASK)}},self.at)
        self.assertEqual(out['state'],'creation_identity_unknown');self.assertFalse(out['native_creation_verified'])
        self.assertIsNone(out['task_id'])

    def test_native_known_rejected_creation_releases_unused_deadline(self):
        self.wire.creation_mode='rejected';out=self.create()
        self.assertEqual(out['state'],'failed');job=self.ledger.get('deadline')
        self.assertEqual(job['state'],'cancelled_creation_rejected');self.assertFalse(job['parent_hold_active'])

    def test_material_deadline_keeps_two_to_four_hours_and_24h_cooldown(self):
        # This is a binding fixture, not a verified material creation compiler.
        body=json.dumps({'advertiser_id':9990001,'ad_id':8012367229158357,'scene':'MATERIAL_ADD_BUDGET','material_ids':[10001],'budget':100})
        for hours in (1,4.5):
            with self.assertRaises(ValueError):self.reserve('invalid',control_type='material_chase',material_id='10001',duration_hours=hours,request_digest=digest(body))
        self.reserve(control_type='material_chase',material_id='10001',duration_hours=2,request_digest=digest(body))
        reply=create_task(body,'PRIVATE_TOKEN',10,lambda:self.wire.now)
        self.ledger.bind_creation('deadline',body,reply,self.at);self.wire.now+=timedelta(hours=2)
        self.wire.status='FROZEN';self.step();self.wire.now+=timedelta(seconds=120);self.step()
        closed=self.wire.now;self.wire.now=closed+timedelta(hours=23)
        with self.assertRaises(ValueError):self.reserve('same-material',parent_plan_id='2002',control_type='material_chase',material_id='10001',duration_hours=2,request_digest=digest(body))
        self.wire.now=closed+timedelta(hours=24)
        self.assertEqual(self.reserve('same-material',parent_plan_id='2002',control_type='material_chase',material_id='10001',duration_hours=2,request_digest=digest(body))['state'],'reserved_before_creation')

    def test_no_raw_tokens_names_or_response_messages_are_persisted(self):
        self.create();self.wire.now+=timedelta(hours=1);self.step()
        with self.ledger.connect() as db:
            text=str(db.execute('SELECT * FROM control_deadlines').fetchall())+str(db.execute('SELECT * FROM control_deadline_journal').fetchall())
        for secret in ('PRIVATE_TOKEN','PRIVATE_MESSAGE','PRIVATE_ERROR','test-stop'):self.assertNotIn(secret,text)

    def test_expired_or_duration_mismatched_reservation_prevents_creation_io(self):
        self.reserve(duration_hours=2)
        permit=ControlCreationPermit('9990001',self.preview['digest'],self.at,self.at+timedelta(minutes=2),'test-only')
        client=ControlCreationClient(self.attempts,enable_network=True,clock=lambda:self.wire.now)
        with self.assertRaises(ValueError):client.dispatch('creation',self.proposal['id'],self.rows,self.cfg,permit,lambda:'PRIVATE_TOKEN',deadline_id='deadline')
        self.assertEqual(self.wire.calls,[])
        self.ledger.cancel_before_creation('deadline',self.at);self.reserve('later')
        self.wire.now+=timedelta(seconds=61)
        with self.assertRaises(ValueError):client.dispatch('creation',self.proposal['id'],self.rows,self.cfg,permit,lambda:'PRIVATE_TOKEN',deadline_id='later')
        self.assertEqual(self.wire.calls,[])

    def test_local_deadline_binding_failure_does_not_erase_the_native_creation_id(self):
        with patch.object(DeadlineLedger,'bind_creation',side_effect=RuntimeError('PRIVATE_ERROR')):out=self.create()
        self.assertEqual(out['created_task_id'],TASK);self.assertTrue(out['creation_receipt']['origin_verified'])
        self.assertFalse(out['deadline_binding_completed']);self.assertEqual(self.ledger.get('deadline')['state'],'reserved_before_creation')
        with self.assertRaises(ValueError):self.ledger.cancel_before_creation('deadline',self.at)

    def test_missing_or_stale_read_origin_cannot_authorize_end(self):
        self.create();self.wire.now+=timedelta(hours=1)
        for mode in ('missing','stale'):
            self.wire.date_mode=mode;out=self.step();self.assertEqual(out['state'],'needs_independent_review')
        self.assertEqual(self.end_calls(),[])

    def test_end_ambiguity_requires_reads_and_not_another_post(self):
        self.create();self.wire.now+=timedelta(hours=1);self.wire.end_mode='ambiguous'
        self.assertEqual(self.step()['state'],'end_unknown');self.wire.now+=timedelta(seconds=120);self.step()
        self.assertEqual(len(self.end_calls()),1)

    def test_late_first_terminal_read_does_not_pair_with_an_old_observation(self):
        self.create();self.wire.now+=timedelta(hours=1);self.wire.status='FROZEN';self.step()
        self.wire.now+=timedelta(seconds=601);self.assertTrue(self.step()['parent_hold_active'])
        self.wire.now+=timedelta(seconds=120);self.assertFalse(self.step()['parent_hold_active'])

    def test_clock_rollback_after_read_preserves_hold_and_prevents_post(self):
        self.create();due=self.at+timedelta(hours=1);self.wire.now=due
        values=iter([due,due,due,due,due-timedelta(seconds=1),due])
        client=DeadlineClient(self.ledger,enable_network=True,clock=lambda:next(values))
        out=self.step(client=client);self.assertEqual(out['state'],'needs_independent_review');self.assertEqual(self.end_calls(),[])
        self.assertTrue(self.ledger.get('deadline')['parent_hold_active'])

    def test_a_bound_material_job_cannot_be_reused_for_a_different_material(self):
        self.reserve(control_type='material_chase',material_id='10001',duration_hours=2)
        body=json.dumps({'advertiser_id':9990001,'ad_id':8012367229158357,'scene':'MATERIAL_ADD_BUDGET','material_ids':[10002]})
        with self.assertRaises(ValueError):self.ledger.bind_creation('deadline',body,{'code':0,'data':{'id':int(TASK)}},self.at)
        self.assertEqual(self.ledger.get('deadline')['state'],'reserved_before_creation')

    def test_acceptance_receipt_sets_final_deadline_without_shortening_the_requested_hour(self):
        self.reserve();clocks=iter([self.at,self.at+timedelta(seconds=10)])
        reply=create_task(self.body,'PRIVATE_TOKEN',10,lambda:next(clocks))
        job=self.ledger.bind_creation('deadline',self.body,reply,self.at+timedelta(seconds=10))
        self.assertEqual(job['deadline_anchor_at'],(self.at+timedelta(seconds=10)).isoformat())
        self.assertEqual(job['due_at'],(self.at+timedelta(hours=1,seconds=10)).isoformat())
        with self.assertRaises(ValueError):self.ledger.change(job,'watching',self.at+timedelta(seconds=10),
            {'due_at':(self.at+timedelta(hours=2)).isoformat()},event='extend')

    def test_natural_terminal_observation_does_not_invent_a_new_parent_mutation_cooldown(self):
        self.create();self.wire.now+=timedelta(hours=1);self.wire.status='FROZEN';self.step()
        self.wire.now+=timedelta(seconds=120);self.step()
        self.attempts.claim('after-natural','different',self.wire.now,{'scope':'plan','account_id':'9990001','entity_id':'8012367229158357'})

    def test_post_end_parent_cooldown_is_scoped_and_supports_older_journal_only_records(self):
        self.create();self.wire.now+=timedelta(hours=1);self.step();self.wire.status='DISABLE'
        self.wire.now+=timedelta(seconds=120);self.step();self.wire.now+=timedelta(seconds=120);self.step()
        record=self.ledger.get('deadline');del record['end_claimed_at']
        with self.ledger.connect() as db:db.execute('UPDATE control_deadlines SET data_json=? WHERE id=?',(json.dumps(record),'deadline'))
        with self.assertRaises(ValueError):self.attempts.claim('blocked','d1',self.wire.now,{'scope':'plan','account_id':'9990001','entity_id':'8012367229158357'})
        self.attempts.claim('other-account','d2',self.wire.now,{'scope':'plan','account_id':'9990002','entity_id':'8012367229158357'})
        self.attempts.claim('other-parent','d3',self.wire.now,{'scope':'plan','account_id':'9990001','entity_id':'2002'})

    def test_parent_cooldown_uses_native_send_time_after_claim_delay(self):
        from monitor.native_control_io import end_task
        self.create();self.wire.now+=timedelta(hours=1)
        def delayed(*args):self.wire.now+=timedelta(seconds=10);return end_task(*args)
        with patch('monitor.control_deadline.end_task',side_effect=delayed):self.step()
        self.wire.status='DISABLE';self.wire.now+=timedelta(seconds=120);self.step()
        self.wire.now+=timedelta(seconds=120);self.step();identity={'scope':'plan','account_id':'9990001','entity_id':'8012367229158357'}
        with self.assertRaises(ValueError):self.attempts.claim('after','new',self.at+timedelta(hours=2),identity)
        self.attempts.claim('after','new',self.at+timedelta(hours=2,seconds=10),identity)

    def test_unknown_end_cooldown_uses_later_closure_instead_of_guessing_send_time(self):
        self.create();self.wire.now+=timedelta(hours=1);self.wire.end_mode='timeout';self.step()
        self.wire.status='DISABLE';self.wire.now+=timedelta(seconds=120);self.step()
        self.wire.now+=timedelta(seconds=120);self.step();identity={'scope':'plan','account_id':'9990001','entity_id':'8012367229158357'}
        with self.assertRaises(ValueError):self.attempts.claim('after','new',self.at+timedelta(hours=2),identity)
        self.attempts.claim('after','new',self.wire.now+timedelta(hours=1),identity)


if __name__=='__main__':unittest.main()
