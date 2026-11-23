"""Run the Windows release against a temporary profile and a loopback test port."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from qcx_issuer import load_key
from qcx_license import License, LicenseError, issue, machine_code, machine_digest


def rejects(action, match):
    try:
        action()
    except LicenseError as exc:
        assert match in str(exc), str(exc)
    else:
        raise AssertionError('预期授权拒绝')


def check_clock(directory, device):
    key = Ed25519PrivateKey.generate()
    now = int(time.time())
    wall = [now]
    elapsed = [0]
    public = key.public_key().public_bytes_raw()
    make = lambda: License(directory, public, device, clock=lambda: wall[0], monotonic=lambda: elapsed[0])
    license = make()
    code = issue(key, machine_code(device), now + 300, now=now)
    license.activate(code)
    assert license.check()['expires'] == now + 300
    wall[0] -= 180
    rejects(license.check, '回拨')
    rejects(make().check, '回拨')
    wall[0] = now + 300
    rejects(license.check, '到期')
    print('PASS 进程内与重启后时间回拨、精确到期')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('archive', type=Path)
    parser.add_argument('--owner', type=Path)
    args = parser.parse_args()
    key = load_key()
    device = machine_digest()
    with tempfile.TemporaryDirectory(prefix='qcx-smoke-') as path:
        temp = Path(path)
        extracted = temp / 'client'
        with zipfile.ZipFile(args.archive) as archive:
            members = archive.namelist()
            assert len(members) == len(set(members))
            assert not any(name.endswith(('.py', '.pyc', '.qcm', '.db', '.dpapi', '.js', '.mjs'))
                           for name in members)
            assert not any('issuer' in name.casefold() for name in members)
            archive.extractall(extracted)
        exe = next(extracted.rglob('千川巡航.exe'))
        encrypted = next(extracted.rglob('data.qcx'))
        assert encrypted.read_bytes()[:5] == b'QCXP\x01'
        env = {**os.environ, 'LOCALAPPDATA': str(temp / 'profile')}
        token_file = temp / 'temporary.qcl'
        data = Path(env['LOCALAPPDATA']) / 'QianchuanCruise'
        workspace = data / 'workspace'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        opener = build_opener(ProxyHandler({}))
        flags = subprocess.CREATE_NO_WINDOW

        def run(*argv):
            return subprocess.run([str(exe), *map(str, argv)], cwd=exe.parent, env=env, timeout=20,
                                  creationflags=flags).returncode

        def activate(code):
            token_file.write_text(code, 'ascii')
            return run('--activate-file', token_file)

        assert run('--check-ui') == 0
        if args.owner:
            owner_folder = temp / 'owner'
            with zipfile.ZipFile(args.owner) as archive:
                names = archive.namelist()
                assert not any(name.endswith(('.dpapi', '.pem', '.qcx')) for name in names)
                archive.extractall(owner_folder)
            owner = next(owner_folder.rglob('千川巡航授权签发工具.exe'))
            completed = subprocess.run([str(owner), '--check-ui'], cwd=owner.parent, timeout=20, creationflags=flags)
            assert completed.returncode == 0
        print('PASS 实际打包的激活界面及签发界面初始化')
        assert run('--serve', '--port', port) == 78
        assert not workspace.exists()
        now = int(time.time())
        other_device = ('F' if device[0] != 'F' else '0') + device[1:]
        assert activate(issue(key, machine_code(other_device), now + 120, now=now)) == 78
        assert activate(issue(Ed25519PrivateKey.generate(), machine_code(device), now + 120, now=now)) == 78
        assert activate(issue(key, machine_code(device), now - 1, now=now - 60)) == 78
        assert not workspace.exists()
        print('PASS 无授权、跨机授权、伪造签名和过期授权均拒绝启动')

        assert activate(issue(key, machine_code(device), now + 120, now=now)) == 0

        def launch():
            process = subprocess.Popen([str(exe), '--supervise', '--port', str(port)], cwd=exe.parent,
                                       env=env, creationflags=flags)
            stop_at = time.monotonic() + 15
            while time.monotonic() < stop_at:
                if process.poll() is not None:
                    raise AssertionError(f'守护器提前退出 {process.returncode}')
                try:
                    with opener.open(base + '/api/release/status', timeout=.8) as response:
                        assert json.load(response)['product'] == 'qianchuan-cruise-release'
                    return process
                except OSError:
                    time.sleep(.3)
            run('--stop', '--port', port)
            path = data / 'launcher-error.log'
            raise AssertionError('发布服务未就绪 ' + (path.read_text('utf-8') if path.exists() else ''))

        process = launch()
        try:
            for route in ('/cruise', '/api-setup', '/api/cruise?view=console', '/rules.json', '/cruise.js'):
                with opener.open(base + route, timeout=6) as response:
                    assert response.status == 200 and response.read(8), route
            req = Request(base + '/api/release/ego-bootstrap', method='POST')
            try:
                opener.open(req, timeout=2)
            except HTTPError as exc:
                assert exc.code == 403
            else:
                raise AssertionError('Ego 未授权下发')
            assert json.loads((workspace / 'config.json').read_text('utf-8'))['accounts'] == []
            assert not any((workspace / child).exists() for child in ('static', 'monitor', 'extension'))
            assert run('--export-extension', temp) == 0
            extension = temp / '千川巡航浏览器扩展'
            assert json.loads((extension / 'manifest.json').read_text('utf-8'))['manifest_version'] == 3
            assert (extension / 'hybrid-dom.js').stat().st_size > 2000
            print('PASS 客户端启动、空配置、工作台及官方 API 接入页面、扩展导出')
            now = int(time.time())
            assert activate(issue(key, machine_code(device), now + 12, now=now)) == 0
            assert process.wait(timeout=25) == 78
            print('PASS 运行中到期退出，守护器停止重启')
        finally:
            if process.poll() is None:
                assert run('--stop', '--port', port) == 0
                process.wait(timeout=10)

        now = int(time.time())
        assert activate(issue(key, machine_code(device), now + 120, now=now)) == 0
        process = launch()
        assert run('--stop', '--port', port) == 0
        assert process.wait(timeout=12) == 0
        print('PASS 续期可重新启动，停止指令只停止本测试后台')
        image = bytearray(encrypted.read_bytes())
        image[-1] ^= 1
        encrypted.write_bytes(image)
        assert run('--serve', '--port', port) == 78
        assert '完整性验证失败' in (data / 'launcher-error.log').read_text('utf-8')
        print('PASS 业务密文遭篡改后拒绝启动')
        check_clock(temp / 'clock', device)


if __name__ == '__main__':
    main()
