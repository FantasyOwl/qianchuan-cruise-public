"""Recovery is local, task-scoped, reversible, and never replays a POST."""
import copy
import json
import threading
import unittest
from datetime import timedelta
from unittest.mock import patch
from tests import test_official_extensions as fixtures
from tests import test_official_rule_auto as rule_fixtures


class ActionRecoveryTests(unittest.TestCase):
    setUp=fixtures.ExtensionTests.setUp
    tearDown=fixtures.ExtensionTests.tearDown
    scanned=fixtures.ExtensionTests.scanned
    prepare=fixtures.ExtensionTests.prepare
    confirm=fixtures.ExtensionTests.confirm
    transport=fixtures.ExtensionTests.transport

    def budget(self):
        self.scanned()
        return self.prepare('task_budget',task_id='10001',value=150)

    @staticmethod
    def failure(tid=10001,status=False):
        return {'results':[{'task_id':tid,'status':status,'error_message':'FAULT-2001: 系统开小差啦，可以再试一次'}]}

    def test_explicit_native_failure_is_terminal_and_never_readback_or_reposted(self):
        r=self.budget()
        def reject(*args):
            self.posts.append(args[1]);return self.failure()
        self.queue.writer=reject
        with patch.object(self.queue,'_readback',side_effect=AssertionError('failure needs no readback')):
            out=self.confirm(r)
        self.assertEqual(out['state'],'rejected')
        self.assertTrue(out['result']['native_rejected'])
        self.assertTrue(out['result']['retryable'])
        self.assertEqual(out['result']['ad_changes'],0)
        with self.assertRaises(ValueError):self.confirm(r)
        self.assertEqual(len(self.posts),1)
        with self.queue.db() as db:
            self.queue._pending(db,{'account_id':self.account['id'],'plan_id':self.pid,'action':'auto_rule_scan'})

    def test_old_failure_resolves_locally_without_token_or_ad_reads(self):
        r=self.budget()
        self.queue._finish(r['id'],'submitted_pending_verification',{'reply':self.failure(),'message':'old pending'})
        with patch.object(self.api,'_token',side_effect=AssertionError('must stay local')):
            out=self.queue.recheck({'id':r['id']})
        self.assertEqual(out['state'],'rejected')
        self.assertEqual(out['result']['reply'],self.failure())
        self.assertEqual(self.posts,[])

    def test_only_exact_boolean_failure_is_proof(self):
        r=self.budget()
        variants=[self.failure(999),self.failure(status=0),self.failure(status='false'),
                  {'results':[]},{'results':self.failure()['results']*2},
                  {'results':[{'task_id':10001}]},{'results':[None]},None]
        for reply in variants:
            with self.subTest(reply=reply):
                self.assertIsNone(self.queue.rejected_reply(r['intent'],reply))
        self.assertIsNone(self.queue.rejected_reply({**r['intent'],'action':'pause_task'},self.failure()))

    def test_cancel_during_preflight_is_fast_and_cannot_resurrect_or_post(self):
        r=self.budget();entered=threading.Event();release=threading.Event();errors=[]
        real=self.queue._fresh
        def fresh(*args):
            out=real(*args);entered.set();release.wait(3);return out
        def confirm():
            try:self.confirm(r)
            except Exception as exc:errors.append(exc)
        self.queue._fresh=fresh
        thread=threading.Thread(target=confirm);thread.start()
        try:
            self.assertTrue(entered.wait(3))
            self.queue.cancel({'id':r['id']})
        finally:release.set();thread.join(4)
        self.assertFalse(thread.is_alive());self.assertTrue(errors)
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')
        self.assertEqual(self.posts,[])

    def test_hold_preserves_ambiguous_record_and_unblocks_only_other_tasks(self):
        r=self.budget();saved={'message':'uncertain response','reply':{'results':[]}}
        self.queue._finish(r['id'],'unknown',saved)
        self.queue.hold_task({'id':r['id'],'account_id':self.account['id']})
        current=self.queue.status()[0]
        self.assertEqual(current['state'],'unknown');self.assertEqual(current['result'],saved)
        other={'account_id':self.account['id'],'plan_id':self.pid,'action':'task_budget','task_id':'10002'}
        with self.queue.db() as db:
            self.queue._pending(db,other)
            with self.assertRaisesRegex(ValueError,'人工跳过'):
                self.queue._pending(db,{**other,'task_id':'10001'},r['id'])
            with self.assertRaisesRegex(ValueError,'卡单处理'):
                self.queue._pending(db,{**other,'action':'plan_budget'})
        self.assertFalse(self.queue.held({**r['intent'],'plan_id':'99'}))
        with self.assertRaises(ValueError):self.queue.hold_task({'id':r['id'],'account_id':'99'})
        self.queue.resume_task({'account_id':self.account['id'],'plan_id':self.pid,'task_id':'10001'})
        self.assertEqual(self.queue.holds(),[])
        with self.queue.db() as db:
            with self.assertRaisesRegex(ValueError,'卡单处理'):self.queue._pending(db,other)
        self.assertEqual(self.posts,[])

    def test_hold_between_preflight_and_submission_prevents_post(self):
        r=self.budget();real=self.queue._fresh
        def fresh(*args):
            out=real(*args)
            # Simulate a concurrent hold based on a previous failed record.
            old={**r,'id':'previous'}
            with self.queue.db() as db:
                row=db.execute('SELECT * FROM api_actions WHERE id=?',(r['id'],)).fetchone()
                db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',
                    ('previous','rejected',row['intent'],row['digest'],row['created_at'],row['expires_at'],row['updated_at'],None))
            self.queue.hold_task({'id':old['id'],'account_id':self.account['id']});return out
        self.queue._fresh=fresh
        with self.assertRaisesRegex(ValueError,'人工跳过'):self.confirm(r)
        self.assertEqual(self.posts,[])

    def test_held_unresolved_budget_keeps_larger_reservation_even_when_task_absent(self):
        r=self.budget();self.queue._finish(r['id'],'unknown',{'message':'uncertain'})
        self.queue.hold_task({'id':r['id'],'account_id':self.account['id']})
        for rows in ([],[{'id':'10001','scene':'MATERIAL_ADD_BUDGET','budget':100}]):
            values=self.queue.budget_tasks_with_holds(self.account['id'],self.pid,rows)
            self.assertEqual(float(values[0]['budget']),150)
        with self.assertRaisesRegex(ValueError,'30%'):
            self.queue._budget_guard({'budget':1000},[],200,self.account['id'],self.pid)
        self.assertEqual(self.queue.auto.status(),[])
        self.assertEqual(self.queue.rule_auto.status(),[])

    def test_status_keeps_pending_older_than_latest_200(self):
        r=self.budget();self.queue._finish(r['id'],'unknown',{})
        with self.queue.db() as db:
            row=db.execute('SELECT * FROM api_actions WHERE id=?',(r['id'],)).fetchone()
            for n in range(205):
                at=(self.queue.clock()+timedelta(seconds=n+1)).isoformat()
                db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',
                    (str(n),'verified',row['intent'],row['digest'],at,at,at,None))
        rows=self.queue.status()
        self.assertEqual(len(rows),201)
        self.assertIn(r['id'],{row['id'] for row in rows})

    def test_new_grant_recovers_explicit_failed_ack_before_fresh_rule_evaluation(self):
        for cfg in self.profile['features'].values():cfg['enabled']=False
        rule_fixtures.RuleAutoTests.f2(self);rule_fixtures.RuleAutoTests.authorize(self)
        self.scanned();r=self.prepare('task_budget',task_id='10001',value=150)
        self.queue._finish(r['id'],'submitted_pending_verification',{'reply':self.failure()})
        rule_fixtures.RuleAutoTests.authorize(self)
        before=self.queue.rule_auto.row(self.account['id'])['grant_id']
        self.queue.rule_auto.tick()
        original=next(row for row in self.queue.status() if row['id']==r['id'])
        self.assertEqual(original['state'],'rejected')
        self.assertEqual(self.queue.rule_auto.row(self.account['id'])['grant_id'],before)
        self.assertEqual(len(self.posts),1)
        self.assertTrue(any(row['state']=='verified' for row in self.queue.status()))
