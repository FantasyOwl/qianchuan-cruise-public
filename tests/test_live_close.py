import copy
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from monitor.live_close import LiveCloseSchedules,money

AID='1999999999999002';PID='8218616704149013'
NOW=datetime(2026,10,26,14,0,tzinfo=timezone.utc)

def surface(now=NOW):
    return dict(account_id=AID,plan_id=PID,group='示例分组A',path='/uni-prom',home_url=f'https://qianchuan.jinritemai.com/uni-prom?aavid={AID}',
        tab_id=123,document_id='document-a',captured_at=now.isoformat(),parent_budget='500000.00',running=True,busy=False,
        control_mode='none',control_budget=None,control_minutes=None,control_created_at=None)

class LiveCloseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.now=NOW;self.row=surface()
        self.policy={'revision':'scope-one','accounts':[{'id':AID,'scene':'live','tab_group':'示例分组A','observe':True}]}
        self.q=LiveCloseSchedules(Path(self.tmp.name)/'close.db',clock=lambda:self.now);self.observe()
    def tearDown(self):self.tmp.cleanup()
    def observe(self,armed=True,epoch='epoch-a'):
        self.row['captured_at']=self.now.isoformat()
        return self.q.observe(dict(protocol=1,worker_id='worker-a',epoch=epoch,revision=self.policy['revision'],armed=armed,surfaces=[self.row]),self.policy)
    def create(self,**changes):
        p=dict(worker_id='worker-a',epoch='epoch-a',account_id=AID,plan_id=PID,off_at=(NOW+timedelta(hours=3)).isoformat(),budget='200',duration_minutes=30);p.update(changes)
        return self.q.create(p,self.policy)
    def approve(self,d):return self.q.approve(dict(id=d['id'],intent_digest=d['intent_digest'],confirmed=True),self.policy)
    def claim(self,epoch='epoch-a'):return self.q.claim(dict(worker_id='worker-a',epoch=epoch),self.policy)['command']
    def due(self,d):self.now=datetime.fromisoformat(d['intent']['trigger_at']);self.observe()
    def begin(self,c,**changes):
        p=dict(id=c['id'],lease=c['lease'],worker_id='worker-a',epoch='epoch-a',observation=self.row);p.update(changes);return self.q.begin(p,self.policy)
    def test_cross_midnight_schedule_is_concrete_and_never_implicitly_approved(self):
        d=self.create(off_at='2026-10-27T00:10:00+08:00')
        self.assertEqual(d['intent']['trigger_at'],'2026-10-26T15:50:00+00:00');self.assertEqual(d['intent']['budget'],'200.00')
        self.assertEqual(d['intent']['duration_minutes'],30);self.assertEqual(d['state'],'draft');self.assertIsNone(self.claim());self.assertFalse(self.q.has_pending())
        self.assertEqual((datetime.fromisoformat(d['expires_at'])-self.now).total_seconds(),900)
    def test_confirmed_schedule_survives_hours_but_never_runs_early(self):
        d=self.create();self.approve(d);self.now+=timedelta(hours=2);self.observe()
        self.assertEqual(self.q.status(self.policy)['actions'][0]['state'],'approved');self.assertIsNone(self.claim());self.assertFalse(self.q.has_pending())
        self.assertEqual(self.observe()['alarms'][0]['id'],d['id']);self.due(d);self.assertTrue(self.q.has_pending());self.assertIsNotNone(self.claim())
    def test_unconfirmed_draft_expires_without_commands(self):
        d=self.create();self.now+=timedelta(minutes=16);self.observe();self.assertEqual(self.q.status(self.policy)['actions'][0]['state'],'expired')
        with self.assertRaises(ValueError):self.approve(d)
    def test_window_missed_does_not_catch_up(self):
        d=self.create();self.approve(d);self.due(d);self.now+=timedelta(seconds=120);self.observe()
        self.assertIsNone(self.claim());self.assertEqual(self.q.status(self.policy)['actions'][0]['state'],'missed');self.assertFalse(self.q.has_pending())
    def test_running_boost_blocks_without_stopping_or_retrying_it(self):
        self.row.update(busy=True,control_mode='boost');self.observe();d=self.create();self.approve(d);self.due(d)
        self.assertIsNone(self.claim());r=self.q.status(self.policy)['actions'][0];self.assertEqual(r['state'],'blocked');self.assertIn('保留原任务',r['receipt']['message'])
        self.row.update(busy=False,control_mode='none');self.observe();self.assertIsNone(self.claim())
    def test_cancelled_future_schedule_never_claims(self):
        d=self.create();self.approve(d);self.q.cancel({'id':d['id']});self.due(d);self.assertIsNone(self.claim())
    def test_confirmation_requires_exact_digest_and_fresh_armed_source(self):
        d=self.create()
        for body in [dict(id=d['id'],intent_digest='wrong',confirmed=True),dict(id=d['id'],intent_digest=d['intent_digest'],confirmed=False)]:
            with self.assertRaises(ValueError):self.q.approve(body,self.policy)
        self.observe(False)
        with self.assertRaises(ValueError):self.approve(d)
    def test_input_validation_parent_cap_and_duplicate_plan(self):
        for changes in [dict(budget='0'),dict(budget='NaN'),dict(budget='200.001'),dict(budget='500001'),dict(duration_minutes=True),dict(duration_minutes=20),dict(off_at='2026-10-26T22:10:00'),dict(off_at=(NOW+timedelta(minutes=21)).isoformat()),dict(account_id='100')]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.create(**changes)
        self.create()
        with self.assertRaises(ValueError):self.create()
    def test_begin_is_once_and_rechecks_current_plan_and_conflicts(self):
        d=self.create();self.approve(d);self.due(d);c=self.claim();self.assertIsNone(self.claim())
        for change in [dict(busy=True),dict(running=False),dict(parent_budget='100'),dict(plan_id='100'),dict(captured_at=(self.now-timedelta(seconds=11)).isoformat())]:
            row={**self.row,**change}
            with self.subTest(change=change),self.assertRaises(ValueError):self.begin(c,observation=row)
        self.assertTrue(self.begin(c)['submit_once'])
        with self.assertRaises(ValueError):self.begin(c)
    def test_policy_change_cancels_and_changed_epoch_requires_current_arm(self):
        d=self.create();self.approve(d);self.due(d);self.observe(epoch='epoch-new')
        with self.assertRaises(ValueError):self.claim()
        self.policy['revision']='changed';self.observe(epoch='epoch-new');self.assertIsNone(self.claim('epoch-new'));self.assertEqual(self.q.status(self.policy)['actions'][0]['state'],'cancelled')
    def test_unknown_receipt_locks_queue_and_is_idempotent(self):
        d=self.create();self.approve(d);self.due(d);c=self.claim();self.begin(c)
        p=dict(id=c['id'],lease=c['lease'],submitted=True,observation=None,message='单次点击，未读回')
        self.assertEqual(self.q.receipt(p)['state'],'unknown');self.assertEqual(self.q.receipt(p)['state'],'unknown');self.assertTrue(self.q.has_pending());self.assertIsNone(self.claim())
        with self.assertRaises(ValueError):self.q.receipt({**p,'message':'changed'})
    def test_only_complete_new_document_readback_verifies(self):
        d=self.create();self.approve(d);self.due(d);c=self.claim();self.begin(c)
        s={**self.row,'document_id':'document-b','busy':True,'control_mode':'control','control_budget':'200.00','control_minutes':30,'control_created_at':self.now.isoformat()}
        with self.q._tx() as db:
            r=db.execute('select * from browser_actions where id=?',(c['id'],)).fetchone()
            for change in [dict(document_id='document-a'),dict(control_mode='boost'),dict(control_budget='201.00'),dict(control_minutes=60),dict(control_created_at=None),dict(account_id='100')]:self.assertFalse(self.q._verified(db,r,{**s,**change}))
        p=dict(id=c['id'],lease=c['lease'],submitted=True,observation=s,message='独立刷新读回')
        self.assertEqual(self.q.receipt(p)['state'],'verified');self.assertFalse(self.q.has_pending())
    def test_unclaimed_receipt_cannot_fake_submit_and_timeout_is_unknown(self):
        d=self.create();self.approve(d);self.due(d);c=self.claim()
        with self.assertRaises(ValueError):self.q.receipt(dict(id=c['id'],lease=c['lease'],submitted=True,observation=None,message='fake'))
        self.now+=timedelta(seconds=91);self.observe();self.assertEqual(self.q.status(self.policy)['actions'][0]['state'],'unknown')
    def test_status_drops_out_of_scope_source_without_crash(self):
        self.policy['accounts']=[];self.assertEqual(self.q.status(self.policy)['workers'][0]['surfaces'],[])

    def test_optional_dom_checks_are_read_only_bounded_and_account_scoped(self):
        p=dict(protocol=1,worker_id='worker-a',epoch='epoch-a',revision=self.policy['revision'],armed=True,surfaces=[],view_checks=[dict(account_id=AID,tab_id=123,code='home_plan_identity_missing',detail={'entry_count':1,'row_count':3,'plan_count':0})])
        self.q.observe(p,self.policy);w=self.q.status(self.policy)['workers'][0];self.assertEqual(w['view_checks'][0]['code'],'home_plan_identity_missing');self.assertEqual(w['surfaces'],[]);self.assertEqual(self.q.status(self.policy)['actions'],[])
        for change in [dict(account_id='100'),dict(tab_id=True),dict(code='<script>bad'),dict(detail={'text':'x'*21000})]:
            bad=copy.deepcopy(p);bad['view_checks'][0].update(change)
            with self.subTest(change=list(change)),self.assertRaises(ValueError):self.q.observe(bad,self.policy)
        self.policy['accounts']=[];self.assertEqual(self.q.status(self.policy)['workers'][0]['view_checks'],[])

    def test_mapped_aweme_is_immutable_and_execution_needs_a_new_detail_check(self):
        self.row.update(aweme_id='79987870170',detail_url=f'https://qianchuan.jinritemai.com/uni-prom/detail?aavid={AID}&adId={PID}',parent_captured_at=self.now.isoformat());self.observe()
        d=self.create();self.assertEqual(d['intent']['aweme_id'],'79987870170');self.approve(d)
        self.now=datetime.fromisoformat(d['intent']['trigger_at']);self.row['parent_captured_at']=self.now.isoformat();self.observe();c=self.claim()
        for change in [dict(aweme_id='79987870171'),dict(parent_captured_at=(self.now-timedelta(seconds=91)).isoformat())]:
            with self.assertRaises(ValueError):self.begin(c,observation={**self.row,**change})
        self.begin(c);self.assertEqual(self.q.status(self.policy)['actions'][0]['state'],'submitting')

    def test_mapping_change_between_draft_and_confirmation_requires_new_review(self):
        self.row.update(aweme_id='79987870170',detail_url=f'https://qianchuan.jinritemai.com/uni-prom/detail?aavid={AID}&adId={PID}',parent_captured_at=self.now.isoformat());self.observe();d=self.create()
        self.row['aweme_id']='79987870171';self.observe()
        with self.assertRaises(ValueError):self.approve(d)

if __name__=='__main__':unittest.main()
