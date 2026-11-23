"""Bind an owned Windows worker to its actual parent's lifetime.

Only the current worker can exit. No PID from a state file is used to kill,
attach to, or recover another process. Creation time prevents PID reuse.
"""
import ctypes
from ctypes import wintypes
import os
import re
import threading

PID_ENV = 'QIANCHUAN_MONITOR_PARENT_PID'
TIME_ENV = 'QIANCHUAN_MONITOR_PARENT_CREATION'
OWNER_ENDED_EXIT = 75
_active_guard = None


class WindowsProcess:
    def __init__(self, pid):
        if os.name != 'nt':
            raise OSError('Windows parent lifetime handles are unavailable')
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        self.api.OpenProcess.restype = wintypes.HANDLE
        self.api.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)]*4))
        self.api.GetProcessTimes.restype = wintypes.BOOL
        self.api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        self.api.WaitForSingleObject.restype = wintypes.DWORD
        self.api.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.api.CloseHandle.restype = wintypes.BOOL
        # SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, no termination rights.
        self.handle = self.api.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not self.handle:
            raise OSError('Cannot observe the specified parent process')

    def creation(self):
        created, ended, kernel, user = [wintypes.FILETIME() for _ in range(4)]
        if not self.api.GetProcessTimes(self.handle, created, ended, kernel, user):
            raise OSError('Cannot verify the parent process creation time')
        return (created.dwHighDateTime << 32) | created.dwLowDateTime

    def wait(self, milliseconds):
        result = self.api.WaitForSingleObject(self.handle, milliseconds)
        if result == 0:
            return True
        if result == 258:
            return False
        raise OSError('Parent lifetime wait failed')

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            self.api.CloseHandle(handle)


def owner_environment():
    """Build environment values for this actual supervisor, not disk state."""
    if os.name != 'nt':
        return {}
    proc = WindowsProcess(os.getpid())
    try:
        return {PID_ENV: str(os.getpid()), TIME_ENV: str(proc.creation())}
    finally:
        proc.close()


class ParentGuard:
    def __init__(self, pid, created, *, process_factory=WindowsProcess, on_exit=None):
        if type(pid) is not int or not 0 < pid <= 0xffffffff or pid == os.getpid():
            raise ValueError('Parent process identity is invalid')
        if type(created) is not int or not 0 < created <= 0xffffffffffffffff:
            raise ValueError('Parent creation identity is invalid')
        self.stop = threading.Event()
        self.thread = None
        self.on_exit = os._exit if on_exit is None else on_exit
        self.process = process_factory(pid)
        try:
            if self.process.creation() != created or self.process.wait(0):
                raise ValueError('Parent process identity is missing, ended or reused')
        except BaseException:
            self.process.close()
            raise

    def start(self):
        if self.thread is not None:
            raise ValueError('Parent guard already started')
        def watch():
            while not self.stop.is_set():
                try:
                    ended = self.process.wait(250)
                except OSError:
                    ended = True
                if ended and not self.stop.is_set():
                    self.on_exit(OWNER_ENDED_EXIT)
                    return
        self.thread = threading.Thread(target=watch, daemon=True, name='owned-parent-lifetime')
        self.thread.start()
        return self

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                # Closing a handle during an active wait is undefined on Windows.
                raise OSError('Parent lifetime wait did not finish')
        self.process.close()


def start_parent_guard(environ=None):
    global _active_guard
    env = os.environ if environ is None else environ
    pid, created = env.get(PID_ENV), env.get(TIME_ENV)
    if pid is None and created is None:
        return None
    if not isinstance(pid, str) or not re.fullmatch(r'[1-9][0-9]{0,9}', pid):
        raise ValueError('Owned worker parent identity is incomplete')
    if not isinstance(created, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', created):
        raise ValueError('Owned worker creation identity is incomplete')
    if int(pid) != os.getppid():
        raise ValueError('Owned worker identity does not match its actual parent')
    guard = ParentGuard(int(pid), int(created)).start()
    _active_guard = guard
    return guard


def parent_guard_status():
    return {'mode': 'parent_handle' if _active_guard else 'standalone',
            'watcher_alive': bool(_active_guard and _active_guard.thread and _active_guard.thread.is_alive()),
            'current_worker_exit_only': True,
            'note': '父进程退出只结束本服务；不证明外层任务可恢复、指标新鲜或7×24覆盖。'}
