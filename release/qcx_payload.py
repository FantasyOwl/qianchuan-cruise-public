"""Open the encrypted application in memory after the client license check."""
import hashlib
import importlib.abc
import importlib.util
import io
import json
import marshal
from pathlib import Path
import secrets
import sys
import threading
import time
import zipfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from qcx_license import LicenseError, atomic_write

MAGIC = b'QCXP\x01'
DOMAIN = b'QianchuanCruise encrypted application\0'
MAX_BOOTSTRAPS = 32


def seal(raw, key):
    nonce = secrets.token_bytes(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, raw, DOMAIN + MAGIC)


class EncryptedModules(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def __init__(self, archive, root):
        self.archive = archive
        self.root = root
        self.names = set(archive.namelist())

    def find_spec(self, fullname, path=None, target=None):
        if not (fullname == 'monitor' or fullname.startswith('monitor.')):
            return None
        name = fullname.replace('.', '/')
        for is_package, relative in ((True, name + '/__init__'), (False, name)):
            member = 'modules/' + relative + '.qcm'
            if member in self.names:
                spec = importlib.util.spec_from_loader(fullname, self, is_package=is_package)
                spec.origin = str(self.root / (relative + '.py'))
                spec.has_location = True
                spec.loader_state = member
                if is_package:
                    spec.submodule_search_locations = [str(Path(spec.origin).parent)]
                return spec
        raise ImportError('缺少已打包业务模块 ' + fullname)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        code = marshal.loads(self.archive.read(module.__spec__.loader_state))
        exec(code, module.__dict__)


class Runtime:
    def __init__(self, payload, key, root, license):
        self.license = license
        self.check()
        self.root = root
        encoded = payload.read_bytes()
        if not encoded.startswith(MAGIC):
            raise LicenseError('发布包损坏，请重新解压。')
        try:
            raw = AESGCM(key).decrypt(encoded[len(MAGIC):len(MAGIC)+12],
                                      encoded[len(MAGIC)+12:], DOMAIN + MAGIC)
            self.archive = zipfile.ZipFile(io.BytesIO(raw))
        except (InvalidTag, zipfile.BadZipFile, ValueError) as exc:
            raise LicenseError('发布包完整性验证失败，请索取原始安装包。') from exc
        self.marker = hashlib.sha256(str(root).casefold().encode('utf-8')).hexdigest()[:20]
        self.lock = threading.Lock()
        self.pending = {}

    def check(self):
        return self.license.check()

    def status(self):
        return {'product': 'qianchuan-cruise-release', 'instance': self.marker,
                'expires_at': self.check()['expires']}

    def asset(self, name):
        return self.archive.read('assets/' + name)

    def install(self):
        self.check()
        sys.meta_path.insert(0, EncryptedModules(self.archive, self.root))
        from monitor.runtime_files import use_packaged_assets
        use_packaged_assets(self.asset)

    def seed(self):
        self.check()
        self.root.mkdir(parents=True, exist_ok=True)
        for name in ('config.json', 'data/execution-strategy.json'):
            target = self.root / name
            if not target.exists():
                atomic_write(target, self.asset('defaults/' + name))
        (self.root / 'data/monitoring').mkdir(exist_ok=True, parents=True)

    def export_extension(self, folder):
        self.check()
        dest = Path(folder) / '千川巡航浏览器扩展'
        dest.mkdir(exist_ok=False)
        for name in self.archive.namelist():
            prefix = 'assets/extension/'
            if name.startswith(prefix):
                relative = name[len(prefix):]
                atomic_write(dest / relative, self.archive.read(name))
        return dest

    def ego_entry(self, port, script, config):
        self.check()
        if script not in ('worker.mjs', 'control-watch.mjs'):
            raise ValueError('未知的 Ego 执行脚本。')
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self.lock:
            self.pending = {k: v for k, v in self.pending.items() if v[0] > now}
            if len(self.pending) >= MAX_BOOTSTRAPS:
                raise ValueError('Ego 工作器启动队列已达到上限。')
            self.pending[token] = (now + 30, script, str(config) if config is not None else None)
        url = f'http://127.0.0.1:{port}/api/release/ego-bootstrap'
        return (
            'const r=await fetch(' + json.dumps(url) + ',{method:"POST",headers:{"X-QCX-Bootstrap":'
            + json.dumps(token) + '},redirect:"error",signal:AbortSignal.timeout(15000)});'
            'if(!r.ok)throw Error("Ego授权启动失败");const p=await r.json();'
            'globalThis.__qcxReleaseRoot=p.root;if(p.config!==null)globalThis.__qcxWorkerConfigPath=p.config;'
            'globalThis.__qcxReleaseActors=p.actors;'
            'await import("data:text/javascript;base64,"+Buffer.from(p.source,"utf8").toString("base64"));'
        )

    def take_ego_payload(self, token):
        self.check()
        with self.lock:
            command = self.pending.pop(token, None)
        if not command or command[0] <= time.monotonic():
            return None
        _, script, config = command
        actors = None
        if script == 'worker.mjs':
            def js(name):
                return self.asset('extension/' + name).decode('utf-8')
            actors = {'detail': js('hybrid-audience-dom.js') + '\n' + js('hybrid-dom.js'),
                      'home': js('hybrid-home-dom.js'), 'probe': js('hybrid-probe-dom.js')}
        return {'root': str(self.root), 'config': config, 'actors': actors,
                'source': self.asset('ego/' + script).decode('utf-8')}
