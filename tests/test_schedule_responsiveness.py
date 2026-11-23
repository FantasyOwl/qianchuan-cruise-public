import threading
import time
import unittest
from datetime import timedelta
from tests import test_official_extensions as fixtures


class ScheduleResponsivenessTests(unittest.TestCase):
    setUp=fixtures.ExtensionTests.setUp
    tearDown=fixtures.ExtensionTests.tearDown
    transport=fixtures.ExtensionTests.transport

    def payload(self):
        return {'account_id':self.account['id'],'plan_id':self.pid,'action':'slowdown_schedule',
            'off_at':(self.queue.clock()+timedelta(hours=2)).isoformat(),'duration_minutes':30,'value':200}

    def with_locks(self,locks,fn):
        acquired=threading.Event();release=threading.Event()
        def hold():
            for lock in locks:lock.acquire()
            acquired.set()
            try:release.wait(3)
            finally:
                for lock in reversed(locks):lock.release()
        thread=threading.Thread(target=hold);thread.start();self.assertTrue(acquired.wait(1))
        try:return fn()
        finally:release.set();thread.join()

    def test_prepare_and_confirm_future_job_do_not_wait_for_scan_or_action_network_lock(self):
        def work():
            start=time.monotonic();r=self.queue.prepare(self.payload())
            out=self.queue.confirm({'id':r['id'],'intent_digest':r['intent_digest'],'confirmed':True})
            self.assertLess(time.monotonic()-start,.5);self.assertEqual(out['state'],'scheduled')
        self.with_locks([self.api.read_lock,self.queue.lock],work)
        self.assertEqual(self.calls,[]);self.assertEqual(self.posts,[])

    def test_claim_gate_busy_is_explicit_and_does_not_queue_a_late_preparation(self):
        def work():
            start=time.monotonic()
            with self.assertRaisesRegex(ValueError,'状态核验'):self.queue.prepare(self.payload())
            self.assertLess(time.monotonic()-start,.5)
        self.with_locks([self.queue.gate],work);self.assertEqual(self.queue.status(),[]);self.assertEqual(self.posts,[])

    def test_connection_state_busy_is_explicit_without_waiting(self):
        def work():
            with self.assertRaisesRegex(ValueError,'本地API连接状态'):self.queue.prepare(self.payload())
        self.with_locks([self.api.lock],work);self.assertEqual(self.posts,[])

    def test_repeated_identical_prepare_reuses_same_unconfirmed_card(self):
        p=self.payload();r=self.queue.prepare(p);again=self.queue.prepare(p)
        self.assertEqual(r['id'],again['id']);self.assertEqual(len(self.queue.status()),1)
        self.assertEqual(self.queue.schedules.status(),[]);self.assertEqual(self.posts,[])

    def test_preparing_identical_confirmed_future_schedule_only_opens_original_record(self):
        p=self.payload();r=self.queue.prepare(p)
        self.queue.confirm({'id':r['id'],'intent_digest':r['intent_digest'],'confirmed':True})
        again=self.queue.prepare(p)
        self.assertEqual(again['id'],r['id']);self.assertEqual(again['state'],'scheduled')
        self.assertEqual(len(self.queue.schedules.status()),1);self.assertEqual(self.posts,[])

    def test_independent_rule_edits_do_not_cancel_an_explicit_off_air_schedule(self):
        r=self.queue.prepare(self.payload());self.profile['features']['f1']['max_roi']=9
        out=self.queue.confirm({'id':r['id'],'intent_digest':r['intent_digest'],'confirmed':True})
        self.assertEqual(out['state'],'scheduled');self.assertEqual(self.posts,[])
        self.profile['features']['f1']['max_roi']=8
        from monitor.schema import parse_time
        self.queue.clock=lambda:parse_time(r['intent']['after']['trigger_at'])
        self.queue.schedules.tick();self.assertEqual(self.queue.schedules.status()[0]['state'],'active_acknowledged')
        self.assertEqual(len(self.posts),1)

    def test_cancel_future_schedule_is_local_and_does_not_wait_for_scan_or_writer(self):
        r=self.queue.prepare(self.payload());self.queue.confirm({'id':r['id'],'intent_digest':r['intent_digest'],'confirmed':True})
        def work():
            start=time.monotonic();self.queue.cancel({'id':r['id']});self.assertLess(time.monotonic()-start,.5)
        self.with_locks([self.api.read_lock,self.queue.lock],work)
        self.assertEqual(self.queue.schedules.status()[0]['state'],'cancelled');self.assertEqual(self.posts,[])

    def test_stale_config_can_only_register_local_job_and_due_time_still_requires_native_read(self):
        self.plan['captured_at']=(self.queue.clock()-timedelta(hours=1)).isoformat()
        def fail(*args):raise ValueError('平台数据当前不可读')
        self.api.read_plans=fail
        r=self.queue.prepare(self.payload());out=self.queue.confirm({'id':r['id'],'intent_digest':r['intent_digest'],'confirmed':True})
        self.assertEqual(out['state'],'scheduled');self.assertEqual(self.calls,[]);self.assertEqual(self.posts,[])
        from monitor.schema import parse_time
        self.queue.clock=lambda:parse_time(r['intent']['after']['trigger_at'])
        self.queue.schedules.tick();self.assertEqual(self.posts,[])
        self.assertEqual(self.queue.schedules.status()[0]['state'],'blocked')

    def test_other_current_action_is_reported_immediately_without_waiting_for_it(self):
        other=self.queue.prepare({'account_id':self.account['id'],'plan_id':self.pid,'action':'pause_plan'})
        with self.queue.db() as db:db.execute("UPDATE api_actions SET state='checking' WHERE id=?",(other['id'],))
        def work():
            with self.assertRaisesRegex(ValueError,'提交中或结果不明'):self.queue.prepare(self.payload())
        self.with_locks([self.queue.lock],work);self.assertEqual(self.posts,[])
