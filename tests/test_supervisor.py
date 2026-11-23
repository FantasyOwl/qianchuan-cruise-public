import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from monitor.supervisor import AlreadyRunning, InstanceLock, Supervisor, request_stop, supervision_status
from monitor.supervisor import pause_background,allow_background,background_paused,main


def wait_for(predicate,timeout=5):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if predicate():return
        time.sleep(.02)
    raise AssertionError('test process condition timed out')


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.threads=[]
        (self.root/'run.py').write_text('import time\nwhile True: time.sleep(.05)\n','utf-8')

    def tearDown(self):
        for supervisor,thread,result in self.threads:
            supervisor.stop_event.set();thread.join(6)
            self.assertFalse(thread.is_alive())
            self.assertTrue(supervisor.child is None or supervisor.child.poll() is not None)
        self.tmp.cleanup()

    def start(self,**kw):
        supervisor=Supervisor(self.root,free_port(),poll_seconds=.02,restart_delays=(.08,.16,.3),**kw)
        result=[]
        thread=threading.Thread(target=lambda:result.append(supervisor.run()))
        self.threads.append((supervisor,thread,result));thread.start()
        wait_for(lambda:supervisor.child is not None and supervision_status(self.root).get('state')=='child_running')
        return supervisor,thread,result

    def test_os_lock_excludes_duplicate_and_releases_after_owner_ends(self):
        path=self.root/'data/supervisor.lock';first=InstanceLock(path).acquire();second=InstanceLock(path)
        try:
            with self.assertRaises(AlreadyRunning):second.acquire()
        finally:first.release()
        second.acquire();second.release()

    def test_real_terminal_child_is_restarted_and_then_stops_without_orphan(self):
        manager,thread,result=self.start();first=manager.child
        first.terminate();first.wait(timeout=3)
        # Popen assigns the new handle before the atomic status-file publish.
        # Wait for that published PID too, rather than asserting mid-transition.
        wait_for(lambda:manager.child is not None and manager.child is not first and manager.child.poll() is None
                 and supervision_status(self.root,manager.child.pid).get('current_process_supervised') is True)
        current=manager.child
        status=supervision_status(self.root,current.pid)
        self.assertTrue(status['manager_lock_held']);self.assertTrue(status['current_process_supervised'])
        self.assertEqual(status['restart_count'],1);self.assertEqual(status['exits_observed'],1)
        stopped=request_stop(self.root,timeout=5);thread.join(3)
        self.assertTrue(stopped['stopped']);self.assertIsNotNone(current.poll());self.assertEqual(result,[0])
        events=[json.loads(line)['event'] for line in (self.root/'data/supervisor-events.log').read_text('utf-8').splitlines()]
        self.assertIn('child_exited',events);self.assertEqual(events.count('child_started'),2)

    def test_alive_child_without_http_response_is_not_restarted(self):
        manager,_,_=self.start();child=manager.child
        # This worker never opens a socket. Missing readiness does not kill it.
        time.sleep(.2)
        self.assertIs(manager.child,child);self.assertEqual(manager.launch_count,1)
        self.assertIsNone(child.poll())

    def test_parent_environment_is_bound_to_actual_manager_not_inherited_disk_identity(self):
        from monitor.parent_guard import PID_ENV,TIME_ENV
        with patch.dict(os.environ,{PID_ENV:'stale-parent',TIME_ENV:'stale-created'}):
            with patch('monitor.supervisor.subprocess.Popen',wraps=subprocess.Popen) as spawn:
                self.start();env=spawn.call_args.kwargs['env']
        if os.name=='nt':
            self.assertEqual(env[PID_ENV],str(os.getpid()));self.assertGreater(int(env[TIME_ENV]),0)
        else:
            self.assertNotIn(PID_ENV,env);self.assertNotIn(TIME_ENV,env)

    def test_occupied_port_is_never_attached_or_killed(self):
        with socket.socket() as external:
            external.bind(('127.0.0.1',0));external.listen()
            manager=Supervisor(self.root,external.getsockname()[1],poll_seconds=.02)
            self.assertEqual(manager.run(),2);self.assertIsNone(manager.child)
            self.assertEqual(supervision_status(self.root)['state'],'port_in_use')
            self.assertGreaterEqual(external.fileno(),0)

    def test_duplicate_manager_does_not_overwrite_active_run_or_spawn(self):
        manager,_,_=self.start();status=supervision_status(self.root)
        other=Supervisor(self.root,free_port())
        self.assertEqual(other.run(),2);self.assertIsNone(other.child)
        self.assertEqual(supervision_status(self.root)['run_id'],status['run_id'])
        self.assertIsNone(manager.child.poll())

    def test_stale_stop_request_is_not_replayed_against_a_new_manager(self):
        (self.root/'data').mkdir();(self.root/'data/supervisor-stop.json').write_text(json.dumps({'run_id':'0'*32}),'utf-8')
        manager,_,_=self.start();time.sleep(.12)
        self.assertIsNone(manager.child.poll());self.assertFalse(manager.stop_event.is_set())

    def test_stale_disk_pid_does_not_authorize_terminating_an_unmanaged_child(self):
        external=subprocess.Popen([sys.executable,str(self.root/'run.py')],creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            data=self.root/'data';data.mkdir()
            (data/'supervisor-state.json').write_text(json.dumps({'state':'child_running','child_pid':external.pid,'run_id':'1'*32}),'utf-8')
            status=supervision_status(self.root,external.pid)
            self.assertEqual(status['state'],'manager_not_observed');self.assertFalse(status['current_process_supervised'])
            self.assertFalse(request_stop(self.root)['requested']);self.assertIsNone(external.poll())
        finally:external.terminate();external.wait(timeout=3)

    def test_lock_and_current_pid_must_both_match_before_marking_service_managed(self):
        manager,_,_=self.start()
        self.assertFalse(supervision_status(self.root,manager.child.pid+100000)['current_process_supervised'])
        self.assertTrue(supervision_status(self.root,manager.child.pid)['current_process_supervised'])

    def test_exit_code_and_backoff_are_recorded_without_immediate_restart_storm(self):
        (self.root/'run.py').write_text('raise SystemExit(7)\n','utf-8')
        manager=Supervisor(self.root,free_port(),poll_seconds=.02,restart_delays=(.2,.4))
        result=[];thread=threading.Thread(target=lambda:result.append(manager.run()))
        self.threads.append((manager,thread,result));thread.start()
        wait_for(lambda:supervision_status(self.root).get('state')=='backoff')
        state=supervision_status(self.root)
        self.assertEqual(state['last_exit_code'],7);self.assertEqual(state['restart_delay_seconds'],.2)
        self.assertEqual(state['launch_count'],1)
        self.assertTrue(request_stop(self.root,timeout=5)['stopped'])
        thread.join(3);self.assertEqual(manager.launch_count,1)

    def test_spawn_exception_logs_type_without_error_message_and_releases_lock(self):
        manager=Supervisor(self.root,free_port())
        with patch('monitor.supervisor.subprocess.Popen',side_effect=OSError('FAKE_SECRET_NOT_FOR_LOG')):
            self.assertEqual(manager.run(),1)
        state=supervision_status(self.root)
        self.assertFalse(state['manager_lock_held']);self.assertEqual(state['state'],'supervisor_error')
        events=(self.root/'data/supervisor-events.log').read_text('utf-8')
        self.assertNotIn('FAKE_SECRET',events);self.assertIn('OSError',events)

    def test_invalid_state_and_stop_nonce_cannot_target_a_process(self):
        data=self.root/'data';data.mkdir();state=data/'supervisor-state.json'
        state.write_text('x'*17000,'utf-8')
        self.assertEqual(supervision_status(self.root)['state'],'not_managed')
        state.write_text('['*1500+']'*1500,'utf-8')
        self.assertEqual(supervision_status(self.root)['state'],'not_managed')
        lock=InstanceLock(data/'supervisor.lock').acquire()
        try:
            state.write_text(json.dumps({'run_id':'invalid','child_pid':os.getpid()}),'utf-8')
            with self.assertRaises(ValueError):request_stop(self.root,timeout=.1)
            self.assertFalse((data/'supervisor-stop.json').exists())
        finally:lock.release()

    def test_persistent_pause_stops_current_owned_child_and_prevents_next_spawn(self):
        manager,thread,result=self.start();child=manager.child
        pause_background(self.root);thread.join(4)
        self.assertFalse(thread.is_alive());self.assertIsNotNone(child.poll());self.assertEqual(result,[0])
        following=Supervisor(self.root,free_port(),poll_seconds=.02)
        self.assertEqual(following.run(),0);self.assertIsNone(following.child)
        self.assertTrue(supervision_status(self.root)['background_paused'])

    def test_malformed_pause_marker_fails_closed_until_explicit_resume(self):
        data=self.root/'data';data.mkdir();marker=data/'manual-stop.json';marker.write_text('invalid','utf-8')
        self.assertTrue(background_paused(self.root));self.assertEqual(Supervisor(self.root,free_port()).run(),0)
        allow_background(self.root);self.assertFalse(background_paused(self.root))

    def test_scheduled_cli_honors_pause_without_constructing_a_manager(self):
        pause_background(self.root)
        with patch('monitor.supervisor.ROOT',self.root),patch('sys.argv',['supervise.py','--scheduled']):
            with patch('monitor.supervisor.Supervisor') as manager:
                self.assertEqual(main(),0);manager.assert_not_called()

    def test_explicit_resume_clears_pause_without_starting_a_child(self):
        pause_background(self.root)
        with patch('monitor.supervisor.ROOT',self.root),patch('sys.argv',['supervise.py','--allow-scheduled']):
            with patch('monitor.supervisor.Supervisor') as manager:
                self.assertEqual(main(),0);manager.assert_not_called()
        self.assertFalse(background_paused(self.root))

    def test_explicit_manual_start_clears_pause_then_runs_owned_manager(self):
        pause_background(self.root)
        with patch('monitor.supervisor.ROOT',self.root),patch('sys.argv',['supervise.py']):
            with patch('monitor.supervisor.Supervisor') as manager:
                manager.return_value.run.return_value=0
                self.assertEqual(main(),0);manager.return_value.run.assert_called_once()
        self.assertFalse(background_paused(self.root))

    def test_missing_entry_exits_without_child_and_no_windows_task_is_registered(self):
        (self.root/'run.py').unlink();manager=Supervisor(self.root,free_port())
        self.assertEqual(manager.run(),1);self.assertIsNone(manager.child)
        self.assertFalse(supervision_status(self.root)['manager_lock_held'])


if __name__=='__main__':unittest.main()
