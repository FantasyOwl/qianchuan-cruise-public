"""Offline device licenses. Receivers only need the Ed25519 public key."""
import base64
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
import uuid
import winreg

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PRODUCT = 'qianchuan-cruise'
DOMAIN = b'QianchuanCruise offline license v1\0'
DEVICE_FORMAT = re.compile(r'QCX1(?:-[0-9A-F]{4}){8}')
MAX_TOKEN = 2048
MIN_CLOCK = 1704067200
MAX_CLOCK = 253402300799
CLOCK_SKEW = 120


class LicenseError(ValueError):
    pass


def data_home():
    return Path(os.environ['LOCALAPPDATA']).resolve() / 'QianchuanCruise'


def machine_digest():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r'SOFTWARE\Microsoft\Cryptography', 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            value, _ = winreg.QueryValueEx(key, 'MachineGuid')
        identity = str(uuid.UUID(value))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise LicenseError('无法读取 Windows 设备标识。') from exc
    return hashlib.sha256(DOMAIN + identity.encode('ascii')).hexdigest()[:32].upper()


def machine_code(digest):
    return 'QCX1-' + '-'.join(digest[i:i+4] for i in range(0, len(digest), 4))


def read_machine_code(code):
    code = code.strip().upper()
    if not DEVICE_FORMAT.fullmatch(code):
        raise LicenseError('机器码格式有误，请完整复制客户端机器码。')
    return code[5:].replace('-', '')


def _encode(value):
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def _decode(text):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', text):
        raise LicenseError('授权码格式有误。')
    value = base64.b64decode(text + '=' * (-len(text) % 4), altchars=b'-_', validate=True)
    if _encode(value) != text:
        raise LicenseError('授权码格式有误。')
    return value


def issue(private_key, request, expires_at, *, now=None):
    now = int(time.time()) if now is None else now
    if type(expires_at) is not int or not MIN_CLOCK <= now < expires_at <= MAX_CLOCK:
        raise LicenseError('到期时间须晚于签发时间。')
    record = {'v': 1, 'product': PRODUCT, 'device': read_machine_code(request),
              'issued': now, 'expires': expires_at, 'id': secrets.token_hex(16)}
    payload = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('ascii')
    signature = private_key.sign(DOMAIN + payload)
    return 'QC1.' + _encode(payload) + '.' + _encode(signature)


def verify(public_key, code, device):
    if not isinstance(code, str) or len(code) > MAX_TOKEN:
        raise LicenseError('授权码格式有误。')
    try:
        prefix, body, sig = code.strip().split('.')
        if prefix != 'QC1':
            raise ValueError()
        payload, signature = _decode(body), _decode(sig)
    except (ValueError, TypeError) as exc:
        raise LicenseError('授权码格式有误。') from exc
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, DOMAIN + payload)
    except InvalidSignature as exc:
        raise LicenseError('授权码签名无效，请向签发方获取有效授权。') from exc
    try:
        record = json.loads(payload)
        if (not isinstance(record, dict)
                or set(record) != {'v', 'product', 'device', 'issued', 'expires', 'id'}
                or type(record['v']) is not int or record['v'] != 1 or record['product'] != PRODUCT
                or type(record['issued']) is not int or type(record['expires']) is not int
                or not MIN_CLOCK <= record['issued'] < record['expires'] <= MAX_CLOCK
                or not re.fullmatch(r'[0-9A-F]{32}', record['device'])
                or not re.fullmatch(r'[0-9a-f]{32}', record['id'])):
            raise ValueError()
    except (TypeError, ValueError, UnicodeError) as exc:
        raise LicenseError('授权内容无效。') from exc
    if not hmac.compare_digest(record['device'], device):
        raise LicenseError('授权码绑定了另一台电脑。')
    return record


class DataBlob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def dpapi(value, purpose, *, decrypt=False):
    """Windows Current User protection for the issuer key and local clock state."""
    def blob(data):
        buf = ctypes.create_string_buffer(data)
        return buf, DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))

    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel.LocalFree.restype = wintypes.HLOCAL
    source_buffer, source = blob(value)
    entropy_buffer, entropy = blob(DOMAIN + purpose)
    target = DataBlob()
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.POINTER(DataBlob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 1, ctypes.byref(target)):
        raise OSError(ctypes.get_last_error(), 'Windows DPAPI 无法处理本地保护数据')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(ctypes.cast(target.data, wintypes.HLOCAL))


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class License:
    def __init__(self, path, public_key, device, *, clock=time.time, monotonic=time.monotonic):
        self.directory = Path(path)
        self.public_key = public_key
        self.device = device
        self.clock = clock
        self.monotonic = monotonic
        self.lock = threading.RLock()
        self.anchor_time = None
        self.anchor_monotonic = None
        self.last_persist = 0
        self.cached = None
        self.license_path = self.directory / 'license.qcl'
        self.clock_path = self.directory / 'clock.dpapi'

    def _time(self):
        now = int(self.clock())
        elapsed = self.monotonic()
        if now < MIN_CLOCK:
            raise LicenseError('系统时间异常，请校准时间后重试。')
        if self.anchor_time is None:
            try:
                state = dpapi(self.clock_path.read_bytes(), self.device.encode('ascii'), decrypt=True)
                saved = int(state.decode('ascii'))
                if not MIN_CLOCK <= saved <= MAX_CLOCK:
                    raise ValueError()
            except FileNotFoundError:
                saved = now
            except (OSError, ValueError) as exc:
                raise LicenseError('无法读取本机授权时间记录。') from exc
            self.anchor_time = saved
            self.anchor_monotonic = elapsed if saved > now else elapsed - (now - saved)
        expected = int(self.anchor_time + elapsed - self.anchor_monotonic)
        if now + CLOCK_SKEW < expected:
            raise LicenseError('检测到系统时间回拨，请恢复正确时间。')
        effective = max(now, expected)
        if effective > expected:
            self.anchor_time, self.anchor_monotonic = effective, elapsed
        return effective

    def _validate_time(self, record, now):
        if now >= record['expires']:
            raise LicenseError('软件授权已到期，请联系签发方续期。')
        if record['issued'] > now + CLOCK_SKEW:
            raise LicenseError('授权签发时间晚于本机时间，请校准时间。')

    def _persist_time(self, now, force=False):
        if force or now >= self.last_persist + 30:
            try:
                atomic_write(self.clock_path, dpapi(str(now).encode('ascii'), self.device.encode('ascii')))
            except OSError as exc:
                raise LicenseError('无法保存本机授权时间记录。') from exc
            self.last_persist = now

    def activate(self, code):
        with self.lock:
            record = verify(self.public_key, code, self.device)
            now = self._time()
            self._validate_time(record, now)
            self._persist_time(now, force=True)
            atomic_write(self.license_path, code.strip().encode('ascii'))
            self.cached = None
            return record

    def check(self):
        with self.lock:
            try:
                stat = self.license_path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
                if self.cached is None or self.cached[0] != signature:
                    if stat.st_size > MAX_TOKEN:
                        raise LicenseError('授权码格式有误。')
                    code = self.license_path.read_text('ascii')
                    self.cached = (signature, verify(self.public_key, code, self.device))
            except FileNotFoundError as exc:
                raise LicenseError('尚未激活，请向签发方提供当前机器码。') from exc
            except (OSError, UnicodeError) as exc:
                raise LicenseError('无法读取授权文件。') from exc
            record = self.cached[1]
            now = self._time()
            self._validate_time(record, now)
            self._persist_time(now)
            return dict(record)
