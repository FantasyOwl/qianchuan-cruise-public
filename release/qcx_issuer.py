"""Owner-only key storage and an offline Windows issuance window."""
from datetime import datetime, timedelta, timezone
import hmac
import os
from pathlib import Path
import sys
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from qcx_license import LicenseError, atomic_write, dpapi, issue

PURPOSE = b'issuer-private-key-v1'
CN = timezone(timedelta(hours=8))


def key_path():
    return Path(os.environ['LOCALAPPDATA']) / 'QianchuanCruiseIssuer' / 'issuer-key.dpapi'


def load_key():
    try:
        secret = dpapi(key_path().read_bytes(), PURPOSE, decrypt=True)
        return Ed25519PrivateKey.from_private_bytes(secret)
    except (OSError, ValueError) as exc:
        raise LicenseError('当前 Windows 用户没有可用的签发密钥。请导入加密备份。') from exc


def load_or_create_key():
    """Build-only bootstrap. The shipped owner tool can only load or restore."""
    path = key_path()
    if path.exists():
        return load_key()
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(dpapi(key.private_bytes_raw(), PURPOSE))
    return key


def own_key(expected):
    key = load_key()
    if not hmac.compare_digest(key.public_key().public_bytes_raw(), expected):
        raise LicenseError('本机签发密钥与本版客户程序不匹配。')
    return key


def password_bytes(text):
    if not text or len(text) < 8:
        raise LicenseError('备份密码至少需要 8 个字符。')
    return text.encode('utf-8')


def main():
    from qcx_issuer_config import PUBLIC_KEY

    window = tk.Tk()
    window.title('千川巡航 · 授权签发')
    window.geometry('670x500')
    window.minsize(590, 440)
    panel = ttk.Frame(window, padding=22)
    panel.pack(fill='both', expand=True)
    ttk.Label(panel, text='授权签发', font=('Microsoft YaHei UI', 17, 'bold')).pack(anchor='w')
    ttk.Label(panel, text='粘贴对方的机器码，选择授权时长。').pack(anchor='w', pady=(4, 15))

    request = tk.StringVar()
    ttk.Label(panel, text='客户机器码').pack(anchor='w')
    ttk.Entry(panel, textvariable=request).pack(fill='x', pady=(4, 10))

    amount = tk.StringVar(value='30')
    end_date = tk.StringVar()
    time_row = ttk.Frame(panel)
    time_row.pack(fill='x', pady=(4, 7))
    ttk.Label(time_row, text='有效期').pack(side='left')
    ttk.Entry(time_row, textvariable=amount, width=9).pack(side='left', padx=8)
    unit = ttk.Combobox(time_row, values=['天', '小时', '分钟'], state='readonly', width=7)
    unit.current(0)
    unit.pack(side='left')
    fixed = tk.BooleanVar(value=False)
    ttk.Checkbutton(panel, text='指定到期时间（北京时间 YYYY-MM-DD HH:MM）', variable=fixed).pack(anchor='w')
    ttk.Entry(panel, textvariable=end_date).pack(fill='x', pady=(4, 10))

    code = tk.Text(panel, height=4, wrap='char', font=('Consolas', 10))
    code.pack(fill='x', pady=(8, 3))
    code.configure(state='disabled')
    result = tk.StringVar(value='')
    ttk.Label(panel, textvariable=result, wraplength=580).pack(anchor='w', pady=(4, 12))
    issued = ''

    def generate():
        nonlocal issued
        try:
            now = int(time.time())
            if fixed.get():
                utc_end = datetime.strptime(end_date.get().strip(), '%Y-%m-%d %H:%M').replace(tzinfo=CN)
                expires = int(utc_end.timestamp())
            else:
                count = int(amount.get())
                if not 1 <= count <= 1_000_000:
                    raise ValueError('请输入正整数时长。')
                expires = now + count * {'天': 86400, '小时': 3600, '分钟': 60}[unit.get()]
            issued = issue(own_key(PUBLIC_KEY), request.get(), expires, now=now)
            code.configure(state='normal')
            code.delete('1.0', 'end')
            code.insert('1.0', issued)
            code.configure(state='disabled')
            result.set('到期时间 ' + datetime.fromtimestamp(expires, CN).strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）')
        except (ValueError, OSError, OverflowError) as exc:
            messagebox.showerror('签发失败', str(exc), parent=window)

    def copy_code():
        if issued:
            window.clipboard_clear()
            window.clipboard_append(issued)
            result.set(result.get().split(' · ')[0] + ' · 已复制授权码')

    def backup():
        try:
            key = own_key(PUBLIC_KEY)
            password = simpledialog.askstring('加密备份', '设置备份密码（至少 8 个字符）', show='*', parent=window)
            if password is None:
                return
            confirm = simpledialog.askstring('加密备份', '再次输入备份密码', show='*', parent=window)
            if password != confirm:
                raise LicenseError('两次密码不一致。')
            password = password_bytes(password)
            name = filedialog.asksaveasfilename(parent=window, defaultextension='.pem',
                                               initialfile='千川巡航签发密钥-加密备份.pem')
            if name:
                pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.BestAvailableEncryption(password))
                atomic_write(Path(name), pem)
                result.set('加密备份已保存。请将备份和密码分别保管。')
        except (ValueError, OSError) as exc:
            messagebox.showerror('备份失败', str(exc), parent=window)

    def restore():
        name = filedialog.askopenfilename(parent=window, filetypes=[('加密备份', '*.pem')])
        if not name:
            return
        try:
            password = simpledialog.askstring('恢复签发密钥', '输入备份密码', show='*', parent=window)
            if password is None:
                return
            file = Path(name)
            if file.stat().st_size > 16384:
                raise LicenseError('密钥备份文件格式无效。')
            key = serialization.load_pem_private_key(file.read_bytes(), password_bytes(password))
            if (not isinstance(key, Ed25519PrivateKey)
                    or not hmac.compare_digest(key.public_key().public_bytes_raw(), PUBLIC_KEY)):
                raise LicenseError('该备份的签发密钥与本版客户端不匹配。')
            atomic_write(key_path(), dpapi(key.private_bytes_raw(), PURPOSE))
            result.set('签发密钥已恢复到当前 Windows 用户。')
        except (ValueError, OSError, TypeError) as exc:
            messagebox.showerror('恢复失败', str(exc), parent=window)

    actions = ttk.Frame(panel)
    actions.pack(fill='x')
    for title, command in (('生成授权码', generate), ('复制授权码', copy_code),
                           ('备份签发密钥', backup), ('导入密钥备份', restore)):
        ttk.Button(actions, text=title, command=command).pack(side='left', padx=(0, 8))
    try:
        own_key(PUBLIC_KEY)
        result.set('本机签发密钥可用。')
    except LicenseError as exc:
        result.set(str(exc))
        if '--check-ui' in sys.argv:
            window.destroy()
            return 78
    if '--check-ui' in sys.argv:
        window.update_idletasks()
        window.destroy()
    else:
        window.mainloop()
    return 0
