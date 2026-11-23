"""Windows launcher for device activation, owned supervision, and asset export."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from urllib.request import ProxyHandler, build_opener
import webbrowser
import winreg

from qcx_client_config import CONTENT_KEY, PUBLIC_KEY
from qcx_license import License, LicenseError, atomic_write, data_home, machine_code, machine_digest
from qcx_payload import Runtime

LICENSE_EXIT = 78
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_NAME = 'QianchuanCruiseLicensed'
CN = timezone(timedelta(hours=8))


def runtime_for(license):
    path = Path(sys._MEIPASS) / 'data.qcx'
    return Runtime(path, CONTENT_KEY, data_home() / 'workspace', license)


def owned_status(root, port):
    url = f'http://127.0.0.1:{port}/api/release/status'
    try:
        opener = build_opener(ProxyHandler({}))
        with opener.open(url, timeout=.75) as reply:
            value = json.load(reply)
        marker = hashlib.sha256(str(root).casefold().encode('utf-8')).hexdigest()[:20]
        return value.get('product') == 'qianchuan-cruise-release' and value.get('instance') == marker
    except (OSError, ValueError, AttributeError):
        return False


def occupied(port):
    with socket.socket() as sock:
        sock.settimeout(.35)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def background_settings(enable=None, port=8877):
    if enable is None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                command, _ = winreg.QueryValueEx(key, RUN_NAME)
            return bool(command)
        except FileNotFoundError:
            return False
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enable:
            command = subprocess.list2cmdline([sys.executable, '--supervise', '--scheduled', '--port', str(port)])
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, command)
        else:
            try:winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:pass
    return enable


def open_window(license, port, *, check_ui=False):
    window = tk.Tk()
    window.title('千川巡航')
    window.geometry('650x430')
    window.minsize(570, 390)
    main = ttk.Frame(window, padding=22)
    main.pack(fill='both', expand=True)
    ttk.Label(main, text='千川巡航', font=('Microsoft YaHei UI', 19, 'bold')).pack(anchor='w')
    status = tk.StringVar()
    ttk.Label(main, textvariable=status, wraplength=580).pack(anchor='w', pady=(4, 17))
    ttk.Label(main, text='本机机器码').pack(anchor='w')
    machine = ttk.Frame(main)
    machine.pack(fill='x', pady=(4, 13))
    request = tk.StringVar(value=machine_code(license.device))
    ttk.Entry(machine, textvariable=request, state='readonly').pack(side='left', fill='x', expand=True)

    def copy_machine():
        window.clipboard_clear()
        window.clipboard_append(request.get())
        note.set('机器码已复制。')

    ttk.Button(machine, text='复制机器码', command=copy_machine).pack(side='left', padx=(8, 0))
    ttk.Label(main, text='粘贴签发方发来的授权码').pack(anchor='w')
    entry = tk.Text(main, height=4, wrap='char', font=('Consolas', 10))
    entry.pack(fill='x', pady=(5, 10))
    controls = ttk.Frame(main)
    controls.pack(fill='x', pady=(0, 8))
    note = tk.StringVar()
    ttk.Label(main, textvariable=note, wraplength=570).pack(anchor='w', pady=(5, 0))
    runtime_root = data_home() / 'workspace'

    def update():
        try:
            record = license.check()
            end = datetime.fromtimestamp(record['expires'], CN).strftime('%Y-%m-%d %H:%M:%S')
            status.set('授权有效至 ' + end + '（北京时间）')
            for button in active_buttons:
                button.state(['!disabled'])
        except LicenseError as exc:
            status.set(str(exc))
            for button in active_buttons:
                button.state(['disabled'])
        window.after(1000, update)

    def activate():
        try:
            license.activate(entry.get('1.0', 'end-1c'))
            entry.delete('1.0', 'end')
            note.set('激活成功，可以打开工作台。')
        except (ValueError, OSError) as exc:
            messagebox.showerror('激活失败', str(exc), parent=window)

    def open_console():
        try:
            license.check()
            if owned_status(runtime_root, port):
                webbrowser.open(f'http://127.0.0.1:{port}/cruise')
                return
            if occupied(port):
                raise LicenseError(f'端口 {port} 正被另一个程序占用。请关闭旧程序，或使用 --port 指定其他端口。')
            worker = subprocess.Popen([sys.executable, '--supervise', '--port', str(port)],
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            note.set('正在启动本地工作台…')
            deadline = time.monotonic() + 18

            def ready():
                if owned_status(runtime_root, port):
                    note.set('工作台已启动。关闭此窗口后，后台可继续运行。')
                    webbrowser.open(f'http://127.0.0.1:{port}/cruise')
                elif worker.poll() is not None or time.monotonic() >= deadline:
                    note.set('启动未完成。可在数据目录查看启动记录。')
                else:
                    window.after(350, ready)

            window.after(350, ready)
        except (ValueError, OSError) as exc:
            messagebox.showerror('工作台启动失败', str(exc), parent=window)

    def stop():
        note.set('正在停止本地后台…')

        def run():
            result = subprocess.run([sys.executable, '--stop', '--port', str(port)], check=False,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            text = '后台已停止。' if result.returncode == 0 else '后台停止未完成，请查看数据目录。'
            try:window.after(0, note.set, text)
            except (RuntimeError, tk.TclError):pass

        threading.Thread(target=run, daemon=True).start()

    def export():
        folder = filedialog.askdirectory(parent=window, title='选择浏览器扩展导出位置')
        if not folder:
            return
        try:
            dest = runtime_for(license).export_extension(folder)
            note.set('已导出。请在 Chrome 扩展页选择“加载已解压的扩展程序”。')
            os.startfile(dest)
        except FileExistsError:
            messagebox.showerror('目录已存在', '请选择另一个位置，原目录已保留。', parent=window)
        except (OSError, ValueError) as exc:
            messagebox.showerror('扩展导出失败', str(exc), parent=window)

    def open_data():
        directory = data_home()
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(directory)

    auto = tk.BooleanVar(value=background_settings())

    def save_auto():
        try:
            license.check()
            background_settings(auto.get(), port)
            note.set('随 Windows 启动：' + ('已开启。' if auto.get() else '已关闭。'))
        except (ValueError, OSError) as exc:
            auto.set(background_settings())
            messagebox.showerror('未保存', str(exc), parent=window)

    active_buttons = []
    for title, command, gated in (
        ('激活或续期', activate, False), ('打开工作台', open_console, True),
        ('停止后台', stop, True), ('导出 Chrome 扩展', export, True), ('数据目录', open_data, False)
    ):
        button = ttk.Button(controls, text=title, command=command)
        button.pack(side='left', padx=(0, 7))
        if gated:
            active_buttons.append(button)
    toggle = ttk.Checkbutton(main, text='随 Windows 登录启动后台', variable=auto, command=save_auto)
    toggle.pack(anchor='w', pady=(8, 0))
    active_buttons.append(toggle)
    update()
    if check_ui:
        window.update_idletasks()
        window.destroy()
    else:
        window.mainloop()


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--serve', action='store_true')
    mode.add_argument('--supervise', action='store_true')
    mode.add_argument('--stop', action='store_true')
    mode.add_argument('--activate-file', type=Path)
    mode.add_argument('--export-extension', type=Path)
    mode.add_argument('--check-ui', action='store_true')
    parser.add_argument('--scheduled', action='store_true')
    parser.add_argument('--port', type=int, default=8877)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        return 2
    unattended = args.serve or args.supervise or args.stop or args.activate_file or args.export_extension
    try:
        license = License(data_home() / 'activation', PUBLIC_KEY, machine_digest())
        if not unattended:
            open_window(license, args.port, check_ui=args.check_ui)
            return 0
        if args.activate_file:
            if args.activate_file.stat().st_size > 2048:
                raise LicenseError('授权码文件过大。')
            license.activate(args.activate_file.read_text('utf-8-sig'))
            return 0
        release = runtime_for(license)
        if args.export_extension:
            release.export_extension(args.export_extension)
            return 0
        release.seed()
        release.install()
        from monitor.supervisor import Supervisor, allow_background, background_paused, pause_background, request_stop
        if args.stop:
            pause_background(release.root)
            result = request_stop(release.root)
            return 0 if result.get('stopped') or not result.get('requested') else 2
        if args.supervise:
            if args.scheduled and background_paused(release.root):
                return 0
            if not args.scheduled:
                allow_background(release.root)
            return Supervisor(release.root, port=args.port, worker_command=[sys.executable, '--serve'],
                              stop_exit_codes=(LICENSE_EXIT,)).run()
        from monitor.parent_guard import start_parent_guard
        from monitor.server import main as serve
        guard = start_parent_guard()
        try:
            return serve(['--port', str(args.port)], root=release.root, release=release)
        finally:
            if guard is not None:
                guard.close()
    except (ValueError, OSError, KeyError, ImportError) as exc:
        if unattended:
            # A failure before authorization never loads application assets.
            try:
                log = data_home() / 'launcher-error.log'
                atomic_write(log, (type(exc).__name__ + ' ' + str(exc) + '\n').encode('utf-8'))
            except OSError:
                pass
        else:
            messagebox.showerror('千川巡航', str(exc))
        return LICENSE_EXIT if isinstance(exc, LicenseError) else 1
