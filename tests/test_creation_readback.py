import copy
import json
import tempfile
import unittest
from datetime import timedelta,timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import Mock,patch
import urllib.parse

from monitor.control_creation_client import ControlCreationClient,ControlCreationPermit
from monitor.control_creation_readback import (FORMAT,CreationTaskReadClient,CreationTaskReadPermit,creation_intent,
    load_creation_attempt,prepare_creation_read_request,compare_creation_task_pages,review_supplied_creation)
from monitor.native_control_io import read_task_page
from monitor.submission import AttemptLedger
from test_control_creation import fixture

TASK='8753264005940210'


def page(row,number=1,size=10,total=None):
    rows=[] if row is None else row if isinstance(row,list) else [row]
    total=len(rows) if total is None else total
    return json.dumps({'code':0,'data':{'task_list':rows,'page_info':{'page':number,'page_size':size,
        'total_number':total,'total_page':(total+size-1)//size}}})


def task_row(state='FROZEN'):
    return {'id':int(TASK),'budget':100,'duration':1,'task_status':state,'create_time':'2026-10-23 16:00:00',
        'external_action_name':'直播间支付','deep_external_action_name':None,'name':'PRIVATE_TASK_NAME'}


class NativeWire:
    """A fake HTTPS server at the urllib boundary, never opens a socket."""
    def __init__(self,at):
        self.now=at;self.calls=[];self.state='FROZEN';self.row=task_row();self.date_mode='ok';self.rid_mode='fresh';self.hook=None

    def opener(self,*handlers):
        wire=self
        class Opener:
            def open(self,request,timeout):
                parsed=urllib.parse.urlsplit(request.full_url)
                if parsed.netloc!='api.oceanengine.com':raise AssertionError('unexpected host')
                wire.calls.append((request.method,request.full_url,timeout));count=len(wire.calls)
                if request.method=='POST':payload={'code':0,'data':{'id':int(TASK)}}
                else:
                    params=urllib.parse.parse_qs(parsed.query);row=copy.deepcopy(wire.row);row['task_status']=wire.state
                    payload=json.loads(page(row,size=int(params['page_size'][0])))
                    if wire.hook:wire.hook()
                if wire.rid_mode!='missing':payload['request_id']='same-rid' if wire.rid_mode=='same' else 'wire-request-'+str(count)
                payload['message']='PRIVATE_NATIVE_RESPONSE_TEXT'
                date=wire.now-timedelta(hours=1) if wire.date_mode=='stale' else wire.now
                headers={} if wire.date_mode=='missing' else {'Date':format_datetime(date.astimezone(timezone.utc),usegmt=True)}
                raw=json.dumps(payload,ensure_ascii=False).encode()
                class Response:
                    def __enter__(self):return self
                    def __exit__(self,*args):pass
                    def geturl(self):return request.full_url
                    def read(self,n):return raw[:n]
                result=Response();result.headers=headers;return result
        return Opener()


class CreationReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'native.db';self.ledger=AttemptLedger(self.path)
        self.rows,self.cfg,self.at,self.proposal,self.preview=fixture();self.wire=NativeWire(self.at)
        self.http=patch('monitor.native_control_io.urllib.request.build_opener',side_effect=self.wire.opener);self.http.start();self.addCleanup(self.http.stop)

    def create(self,*,native=True):
        permit=ControlCreationPermit('9990001',self.preview['digest'],self.at,self.at+timedelta(minutes=2),'test-create')
        transport=None if native else lambda *args:{'code':0,'data':{'id':int(TASK)}}
        out=ControlCreationClient(self.ledger,enable_network=True,transport=transport,clock=lambda:self.wire.now).dispatch(
            'creation',self.proposal['id'],self.rows,self.cfg,permit,lambda:'PRIVATE_TOKEN')
        self.assertTrue(out['business_accepted']);return out

    def attempt(self):return load_creation_attempt(self.ledger,'creation')

    def request(self):
        return prepare_creation_read_request(self.attempt(),self.cfg,self.at-timedelta(minutes=1),self.wire.now,max_pages=5,page_size=10)

    def permit(self,request,*,allow=True):
        return CreationTaskReadPermit('9990001',request['digest'],self.wire.now,self.wire.now+timedelta(minutes=2),'test-read',allow)

    def read(self,*,allow=True,transport=None,client=None):
        request=self.request();client=client or CreationTaskReadClient(enable_network=True,transport=transport,clock=lambda:self.wire.now)
        return client.review_attempt(self.ledger,'creation',self.cfg,self.permit(request,allow=allow),lambda:'PRIVATE_TOKEN',
            self.at-timedelta(minutes=1),self.wire.now,max_pages=5,page_size=10)

    def supplied(self,*,state='FROZEN'):
        self.wire.now=self.at+timedelta(minutes=70);request=self.request()
        q={k:request[k] for k in ('account_id','parent_plan_id','marketing_goal','scene','task_id','window_start','window_end','max_pages','page_size')}
        q.update(request_started_at=self.wire.now.isoformat(),received_at=self.wire.now.isoformat())
        return [page(task_row(state))],q,self.attempt()

    def state(self):
        with self.ledger.connect() as db:return db.execute("SELECT state FROM submission_attempts WHERE id='creation'").fetchone()[0]

    def test_native_creation_keeps_qualified_origin_without_raw_response_or_token(self):
        out=self.create();self.assertTrue(out['creation_receipt']['origin_verified'])
        self.assertTrue(creation_intent(self.attempt())['trusted_creation_receipt'])
        with self.ledger.connect() as db:text=str(db.execute('SELECT * FROM submission_attempts').fetchall())
        self.assertNotIn('PRIVATE_TOKEN',text);self.assertNotIn('PRIVATE_NATIVE_RESPONSE_TEXT',text)
        self.assertEqual(self.wire.calls[0][0],'POST');self.assertEqual(self.wire.calls[0][2],10)

    def test_supplied_match_preserves_unknown_even_with_a_claimed_verified_creation_receipt(self):
        self.create();pages,q,attempt=self.supplied()
        result=review_supplied_creation({'format':FORMAT,'attempt':attempt,'query_context':q,'raw_pages':pages},self.cfg,self.wire.now)
        self.assertEqual(result['comparison']['status'],'matches_requested_configuration')
        self.assertEqual(result['comparison']['task_id'],TASK);self.assertFalse(result['can_release_hold'])
        self.assertFalse(result['attempt_state_changed']);self.assertEqual(self.state(),'unknown')
        self.assertIsNone(result['comparison']['platform_created_at']);self.assertNotIn('PRIVATE_TASK_NAME',json.dumps(result))

    def test_exact_task_id_not_name_or_parent_id_is_required(self):
        self.create();pages,q,a=self.supplied();row=task_row();row.update(id=8012367229158357,name=TASK)
        result=compare_creation_task_pages([page(row)],q,a,self.wire.now)
        self.assertEqual(result['status'],'inconclusive');self.assertIn('准确新任务ID','；'.join(result['blockers']))

    def test_context_body_digest_scene_and_new_task_receipt_must_match(self):
        self.create();pages,q,a=self.supplied()
        for mutate in (lambda x:x['context'].update(entity_id=TASK),lambda x:x['result'].update(business_accepted=False),
            lambda x:x['context'].update(request_body_json='{}'),lambda x:x['context'].update(marketing_goal='VIDEO_PROM_GOODS')):
            bad=copy.deepcopy(a);mutate(bad)
            with self.assertRaises(ValueError):compare_creation_task_pages(pages,q,bad,self.wire.now)

    def test_budget_duration_and_purchase_labels_are_not_inferred_from_status(self):
        self.create();_,q,a=self.supplied()
        for field,value in [('budget',101),('duration',2),('external_action_name','直播间人气'),('deep_external_action_name','支付ROI')]:
            row=task_row();row[field]=value;r=compare_creation_task_pages([page(row)],q,a,self.wire.now)
            self.assertNotEqual(r['status'],'matches_requested_configuration')
        row=task_row();row.pop('deep_external_action_name')
        self.assertEqual(compare_creation_task_pages([page(row)],q,a,self.wire.now)['status'],'inconclusive')

    def test_empty_list_unknown_status_and_missing_pages_never_prove_terminal(self):
        self.create();_,q,a=self.supplied()
        r=compare_creation_task_pages([page(None)],q,a,self.wire.now);self.assertEqual(r['status'],'inconclusive')
        row=task_row('PAUSE');self.assertEqual(compare_creation_task_pages([page(row)],q,a,self.wire.now)['status'],'inconclusive')
        rows=[task_row() for _ in range(10)]
        for n,row in enumerate(rows):row['id']=int(TASK)+n
        q['max_pages']=1;r=compare_creation_task_pages([page(rows,total=11)],q,a,self.wire.now)
        self.assertFalse(r['pagination_exhausted']);self.assertEqual(r['status'],'inconclusive')

    def test_page_duplicates_or_changed_totals_are_rejected(self):
        self.create();_,q,a=self.supplied();rows=[task_row() for _ in range(10)]
        with self.assertRaises(ValueError):compare_creation_task_pages([page(rows,total=11),page(task_row(),number=2,total=11)],q,a,self.wire.now)

    def test_query_identity_window_and_credential_fields_are_rejected(self):
        self.create();pages,q,a=self.supplied()
        for field,value in [('task_id','77'),('scene','MATERIAL_ADD_BUDGET'),('parent_plan_id','77'),('received_at',(self.wire.now+timedelta(seconds=1)).isoformat())]:
            bad={**q,field:value}
            with self.assertRaises(ValueError):compare_creation_task_pages(pages,bad,a,self.wire.now)
        payload={'format':FORMAT,'attempt':a,'query_context':q,'raw_pages':pages,'Access-Token':'PRIVATE'}
        with self.assertRaises(ValueError):review_supplied_creation(payload,self.cfg,self.wire.now)

    def test_default_off_reader_does_not_touch_clock_ledger_or_credentials(self):
        client=CreationTaskReadClient(clock=lambda:(_ for _ in ()).throw(AssertionError()))
        token=Mock()
        with self.assertRaises(PermissionError):client.review_attempt(None,'none',{},None,token,self.at,self.at)
        token.assert_not_called();self.assertEqual(self.wire.calls,[])

    def test_first_native_frozen_read_keeps_hold_and_second_reconciles_after_reopening(self):
        self.create();self.wire.now=self.at+timedelta(seconds=3720)
        first=self.read();self.assertTrue(first['ok'],first);self.assertTrue(first['native_read_origin_verified'])
        self.assertFalse(first['attempt_state_changed']);self.assertEqual(self.state(),'unknown')
        self.ledger=AttemptLedger(self.path);self.wire.now+=timedelta(seconds=120)
        second=self.read();self.assertTrue(second['attempt_state_changed'],second)
        self.assertEqual(self.state(),'reconciled_terminal');self.assertFalse(second['parent_hold_active'])
        self.assertEqual(len(second['supporting_observation_ids']),2)
        self.ledger.claim('later-parent','later-parent',self.wire.now,{'account_id':'9990001','scope':'plan','entity_id':'8012367229158357'})
        with self.ledger.connect() as db:text=str(db.execute('SELECT observation_json FROM control_creation_observations').fetchall())
        self.assertNotIn('PRIVATE_TOKEN',text);self.assertNotIn('PRIVATE_NATIVE_RESPONSE_TEXT',text)
        self.assertEqual([c[0] for c in self.wire.calls],['POST','GET','GET'])

    def test_no_local_transition_without_explicit_reconciliation_scope(self):
        self.create();self.wire.now=self.at+timedelta(seconds=3720);self.read(allow=False)
        self.wire.now+=timedelta(seconds=120);out=self.read(allow=False)
        self.assertFalse(out['attempt_state_changed']);self.assertEqual(out['state_transition_reason'],'read_authorized_without_state_transition')

    def test_cooldown_is_not_replaced_by_two_early_frozen_samples(self):
        self.create();self.wire.now=self.at+timedelta(minutes=5);self.read()
        self.wire.now+=timedelta(minutes=2);out=self.read();self.assertFalse(out['attempt_state_changed'])
        self.assertEqual(out['state_transition_reason'],'creation_cooldown_not_elapsed')

    def test_custom_plaintext_reads_cannot_clear_a_real_origin_parent_hold(self):
        self.create();self.wire.now=self.at+timedelta(seconds=3720)
        for _ in range(2):
            out=self.read(transport=lambda *args:page(task_row()));self.assertFalse(out['native_read_origin_verified'])
            self.assertFalse(out['attempt_state_changed']);self.wire.now+=timedelta(seconds=120)
        self.assertEqual(self.state(),'unknown')

    def test_native_reads_cannot_upgrade_an_unverified_creation_origin(self):
        self.create(native=False);self.wire.now=self.at+timedelta(seconds=3720);self.read()
        self.wire.now+=timedelta(seconds=120);out=self.read()
        self.assertTrue(out['native_read_origin_verified']);self.assertFalse(out['creator_receipt_verified'])
        self.assertFalse(out['attempt_state_changed']);self.assertEqual(self.state(),'unknown')

    def test_missing_or_stale_http_dates_and_request_ids_keep_unknown(self):
        for mode in ('missing','stale'):
            with self.subTest(mode=mode):
                self.wire.date_mode=mode
                if not self.wire.calls:self.create()
                self.wire.now=self.at+timedelta(seconds=3720);out=self.read()
                self.assertFalse(out['native_read_origin_verified']);self.assertFalse(out['attempt_state_changed'])
        self.wire.date_mode='ok';self.wire.rid_mode='missing';out=self.read()
        self.assertFalse(out['native_read_origin_verified'])

    def test_running_time_offline_manual_disable_and_other_states_do_not_release(self):
        self.create();self.wire.now=self.at+timedelta(seconds=3720)
        for state in ('PROCESSING','OFFLINE_TIME','DISABLE','OFFLINE_BALANCE','OFFLINE_BUDGET','ROI2_DISABLE','DELETED'):
            self.wire.state=state;out=self.read();self.assertFalse(out['attempt_state_changed'],state)
            self.wire.now+=timedelta(seconds=120)

    def test_newer_running_read_resets_the_terminal_pair_instead_of_selecting_older_frozen(self):
        self.create();self.wire.now=self.at+timedelta(seconds=3720);self.read()
        self.wire.now+=timedelta(seconds=120);self.wire.state='PROCESSING';self.read()
        self.wire.now+=timedelta(seconds=120);self.wire.state='FROZEN'
        self.assertFalse(self.read()['attempt_state_changed'])
        self.wire.now+=timedelta(seconds=120);self.assertTrue(self.read()['attempt_state_changed'])

    def test_reused_request_log_id_does_not_form_independent_pair(self):
        self.create();self.wire.rid_mode='same';self.wire.now=self.at+timedelta(seconds=3720);self.read()
        self.wire.now+=timedelta(seconds=120);self.assertFalse(self.read()['attempt_state_changed'])

    def test_replaying_an_opaque_old_response_cannot_be_a_new_direct_observation(self):
        self.create();self.wire.now=self.at+timedelta(seconds=3720);saved=[]
        def transport(params,page_no,token,timeout):
            if not saved:saved.append(read_task_page(params,page_no,token,timeout,lambda:self.wire.now))
            return saved[0]
        self.read(transport=transport);self.wire.now+=timedelta(seconds=120)
        out=self.read(transport=transport);self.assertFalse(out['attempt_state_changed']);self.assertFalse(out['native_read_origin_verified'])

    def test_timeout_and_scope_mutation_do_not_transition(self):
        self.create();self.wire.now=self.at+timedelta(seconds=3720)
        out=self.read(transport=Mock(side_effect=TimeoutError('PRIVATE_TOKEN')))
        self.assertFalse(out['ok']);self.assertNotIn('PRIVATE_TOKEN',json.dumps(out))
        self.wire.hook=lambda:self.cfg['accounts'][0].update(observe=False)
        out=self.read();self.assertFalse(out['ok']);self.assertEqual(self.state(),'unknown')

    def test_concurrent_attempt_change_is_not_overwritten_or_used_for_reconciliation(self):
        self.create();self.wire.now=self.at+timedelta(seconds=3720)
        def change():
            result=self.attempt()['result'];result['review_note']='changed-by-another-local-worker'
            self.ledger.finish('creation','unknown',result)
        self.wire.hook=change
        out=self.read();self.assertFalse(out['ok']);self.assertEqual(self.state(),'unknown')
        self.assertEqual(self.attempt()['result']['review_note'],'changed-by-another-local-worker')
        with self.ledger.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM control_creation_observations').fetchone()[0],0)

    def test_unqualified_read_after_trusted_creation_cannot_support_release(self):
        self.create();self.wire.now=self.at+timedelta(seconds=3720)
        for mode in ('missing','stale'):
            self.wire.date_mode=mode
            result=self.read();self.assertTrue(result['ok']);self.assertFalse(result['native_read_origin_verified'])
            self.assertFalse(result['attempt_state_changed']);self.wire.now+=timedelta(seconds=120)
        self.wire.date_mode='ok'
        self.assertFalse(self.read()['attempt_state_changed'])

    def test_wrong_permit_or_missing_new_task_id_rejects_before_reading_credentials(self):
        self.create();self.wire.now=self.at+timedelta(seconds=3720);request=self.request();token=Mock()
        permit=CreationTaskReadPermit('other',request['digest'],self.wire.now,self.wire.now+timedelta(minutes=1),'r')
        client=CreationTaskReadClient(enable_network=True,clock=lambda:self.wire.now)
        with self.assertRaises(PermissionError):client.review_attempt(self.ledger,'creation',self.cfg,permit,token,self.at,self.wire.now,max_pages=5,page_size=10)
        token.assert_not_called()
        result=self.attempt()['result'];result['created_task_id']=None;self.ledger.finish('creation','unknown',result)
        with self.assertRaises(ValueError):self.request()

    def test_read_permission_expiry_and_clock_rollback_preserve_attempt(self):
        self.create();self.wire.now=self.at+timedelta(seconds=3720);request=self.request()
        for later in (self.wire.now-timedelta(seconds=1),self.wire.now+timedelta(minutes=3)):
            times=iter([self.wire.now,later]);client=CreationTaskReadClient(enable_network=True,clock=lambda:next(times))
            out=client.review_attempt(self.ledger,'creation',self.cfg,self.permit(request),lambda:'PRIVATE_TOKEN',
                self.at-timedelta(minutes=1),self.wire.now,max_pages=5,page_size=10)
            self.assertFalse(out['ok']);self.assertEqual(self.state(),'unknown')


if __name__=='__main__':unittest.main()
