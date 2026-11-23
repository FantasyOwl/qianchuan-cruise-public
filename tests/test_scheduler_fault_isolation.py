import threading
import unittest
from types import SimpleNamespace
from monitor.server import App

class SchedulerIsolationTests(unittest.TestCase):
    def test_one_failed_component_never_skips_other_recovery_control_or_account_reader(self):
        for failed in range(6):
            calls=[];stop=threading.Event()
            def step(index):
                def run():
                    calls.append(index)
                    if index==failed:raise ValueError('injected fault')
                return run
            def poll_next(accounts):calls.append('account_reader');stop.set()
            a=SimpleNamespace(hybrid=SimpleNamespace(tick=step(0)),hybrid_rules=SimpleNamespace(tick=step(1)),schedules=SimpleNamespace(tick=step(2)),tick_followups=step(3),rule_auto=SimpleNamespace(tick=step(4)),auto=SimpleNamespace(tick=step(5)))
            app=SimpleNamespace(stop_event=stop,official_actions=a,official_api=SimpleNamespace(poll_next=poll_next),cfg=lambda:{'accounts':[]})
            with self.assertLogs('qianchuan_monitor',level='ERROR'):App.poll_official(app)
            self.assertEqual(calls,[0,1,2,3,4,5,'account_reader'])
