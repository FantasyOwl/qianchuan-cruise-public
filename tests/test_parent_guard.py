import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from monitor.parent_guard import (ParentGuard, WindowsProcess, owner_environment,
    start_parent_guard, PID_ENV, TIME_ENV, OWNER_ENDED_EXIT)


class FakeProcess:
    def __init__(self, pid):
        self.pid=pid;self.closed=False;self.ended=threading.Event();self.failure=False
    def creation(self):return 12345
    def wait(self, ms):
        if self.failure:raise OSError('do not echo details')
        return self.ended.wait(ms/1000)
    def close(self):self.closed=True


class ParentGuardTests(unittest.TestCase):
    def test_live_parent_then_termination_exits_only_current_worker(self):
        fake=FakeProcess(123);done=threading.Event();codes=[]
        guard=ParentGuard(123,12345,process_factory=lambda _:fake,on_exit=lambda c:(codes.append(c),done.set())).start()
        try:
            self.assertFalse(done.wait(.05));fake.ended.set();self.assertTrue(done.wait(1))
            self.assertEqual(codes,[OWNER_ENDED_EXIT])
        finally:guard.close()
        self.assertTrue(fake.closed)

    def test_stop_joins_waiter_before_closing_handle_and_does_not_exit(self):
        fake=FakeProcess(123);codes=[]
        guard=ParentGuard(123,12345,process_factory=lambda _:fake,on_exit=codes.append).start()
        guard.close()
        self.assertFalse(guard.thread.is_alive());self.assertTrue(fake.closed);self.assertEqual(codes,[])

    def test_reused_or_already_ended_parent_never_starts_watcher(self):
        for ended,created in [(False,12346),(True,12345)]:
            fake=FakeProcess(123)
            if ended:fake.ended.set()
            with self.assertRaises(ValueError):ParentGuard(123,created,process_factory=lambda _:fake)
            self.assertTrue(fake.closed)

    def test_wait_failure_fails_closed_without_exposing_error_text(self):
        fake=FakeProcess(123);done=threading.Event();codes=[]
        fake.failure=True
        # First zero-time identity check must succeed; failure starts afterwards.
        fake.failure=False;guard=ParentGuard(123,12345,process_factory=lambda _:fake,on_exit=lambda c:(codes.append(c),done.set()))
        fake.failure=True;guard.start()
        try:self.assertTrue(done.wait(1));self.assertEqual(codes,[OWNER_ENDED_EXIT])
        finally:guard.close()

    def test_invalid_parent_identity_rejected_without_opening_process(self):
        for pid,created in [(True,1),(0,1),(2**32,1),(os.getpid(),1),(123,False),(123,0),(123,2**64)]:
            with self.subTest(pid=pid,created=created):
                with patch('monitor.parent_guard.WindowsProcess') as factory:
                    with self.assertRaises(ValueError):ParentGuard(pid,created,process_factory=factory)
                    factory.assert_not_called()

    def test_partial_malformed_or_unrelated_environment_is_rejected(self):
        for env in [{PID_ENV:'123'},{TIME_ENV:'12345'},{PID_ENV:'0123',TIME_ENV:'12345'},
                    {PID_ENV:'1e2',TIME_ENV:'12345'},{PID_ENV:'123',TIME_ENV:True},
                    {PID_ENV:str(os.getpid()),TIME_ENV:'12345'}]:
            with self.assertRaises(ValueError):start_parent_guard(env)

    def test_standalone_start_has_no_watchdog_and_no_environment_mutation(self):
        env={'arbitrary':'kept'};before=copy.deepcopy(env)
        self.assertIsNone(start_parent_guard(env));self.assertEqual(env,before)

    @unittest.skipUnless(os.name=='nt','Windows native process lifetime test')
    def test_native_current_process_handle_creation_wait_and_release(self):
        p=WindowsProcess(os.getpid())
        self.assertGreater(p.creation(),0);self.assertFalse(p.wait(0));p.close();p.close()
        values=owner_environment();self.assertEqual(values[PID_ENV],str(os.getpid()))
        self.assertGreater(int(values[TIME_ENV]),0)

    @unittest.skipUnless(os.name=='nt','Windows native parent-death integration')
    def test_real_worker_exits_after_owned_manager_abrupt_death_without_orphan(self):
        source=str(Path(__file__).resolve().parent.parent)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ready=root/'ready.json';end=root/'end'
            worker=('import sys,os,json,time,threading;from pathlib import Path;'
                f'sys.path.insert(0,{source!r});'
                'from monitor.parent_guard import start_parent_guard,WindowsProcess;'
                'g=start_parent_guard();p=WindowsProcess(os.getpid());'
                'created=p.creation();p.close();'
                'threading.Timer(12,lambda:os._exit(99)).start();'
                f'Path({str(ready)!r}).write_text(json.dumps({{"pid":os.getpid(),"created":created}}));'
                'time.sleep(30)')
            manager=root/'manager.py'
            manager.write_text('import os,sys,subprocess,time\nfrom pathlib import Path\n'
                f'sys.path.insert(0,{source!r})\nfrom monitor.parent_guard import owner_environment\n'
                f'p=subprocess.Popen([sys.executable,"-c",{worker!r}],env={{**os.environ,**owner_environment()}},creationflags=subprocess.CREATE_NO_WINDOW)\n'
                f'while not Path({str(end)!r}).exists():time.sleep(.02)\nos._exit(9)\n',encoding='utf-8')
            process=subprocess.Popen([sys.executable,str(manager)],creationflags=subprocess.CREATE_NO_WINDOW)
            child=None
            try:
                deadline=time.monotonic()+5
                while not ready.exists() and process.poll() is None and time.monotonic()<deadline:time.sleep(.02)
                self.assertTrue(ready.exists());d=json.loads(ready.read_text());child=WindowsProcess(d['pid'])
                self.assertEqual(child.creation(),d['created']);self.assertFalse(child.wait(0))
                end.touch();self.assertEqual(process.wait(timeout=5),9)
                self.assertTrue(child.wait(5000))
            finally:
                if process.poll() is None:process.terminate();process.wait(timeout=3)
                if child:
                    self.assertTrue(child.wait(15000));child.close()


if __name__=='__main__':unittest.main()
