"""Build the Windows x64 client and the separate owner tool from allowlisted files."""
import argparse
import ast
import hashlib
import importlib.util
import io
import json
import marshal
from pathlib import Path
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

from qcx_issuer import load_or_create_key
from qcx_payload import seal

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
CLIENT_NAME = '千川巡航'
OWNER_NAME = '千川巡航授权签发工具'


def code_imports(path):
    for node in ast.walk(ast.parse(path.read_text('utf-8-sig'))):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module
            # Native extensions hide imports from PyInstaller's bytecode scan.
            # A from-import may name a child module such as tkinter.filedialog.
            if node.module.startswith(('monitor', 'qcx_', '__')):
                continue
            for alias in node.names:
                if not alias.name.isidentifier():
                    continue
                qualified = node.module + '.' + alias.name
                try:child = importlib.util.find_spec(qualified)
                except (ImportError, AttributeError, ValueError):child = None
                if child is not None:
                    yield qualified


def transform(esbuild, source, *, bundle=False):
    options = ['--minify-whitespace', '--minify-identifiers', '--legal-comments=inline']
    if not bundle:
        options = ['--minify-whitespace', '--legal-comments=inline']
    else:
        options += ['--bundle', '--platform=node', '--format=esm', '--target=node22']
    return subprocess.run([str(esbuild), str(source), *options],
                          stdout=subprocess.PIPE, check=True).stdout


def build_payload(esbuild, key):
    raw = io.BytesIO()
    imports = set()
    with zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted((PROJECT / 'monitor').rglob('*.py')):
            name = path.relative_to(PROJECT).as_posix()
            source = path.read_text('utf-8-sig')
            filename = '<千川巡航>/' + name
            module = ast.parse(source, filename)
            if name == 'monitor/accounts.py':
                legacy = next(node for node in module.body if isinstance(node, ast.Assign) and
                              any(isinstance(target, ast.Name) and target.id == 'LEGACY_SCOPE' for target in node.targets))
                legacy.value = ast.copy_location(ast.List(elts=[], ctx=ast.Load()), legacy.value)
            compiled = compile(module, filename, 'exec', dont_inherit=True)
            archive.writestr('modules/' + name.removesuffix('.py') + '.qcm', marshal.dumps(compiled))
            imports.update(code_imports(path))
        for folder, suffixes in [('static', {'.js', '.css', '.html', '.png'}),
                                 ('extension', {'.js', '.css', '.html', '.json'})]:
            for path in sorted((PROJECT / folder).iterdir()):
                if not path.is_file() or path.suffix not in suffixes:
                    continue
                value = transform(esbuild, path) if path.suffix == '.js' else path.read_bytes()
                archive.writestr('assets/' + path.relative_to(PROJECT).as_posix(), value)
        for worker in ('worker.mjs', 'control-watch.mjs'):
            value = transform(esbuild, PROJECT / 'monitor' / 'ego' / worker, bundle=True)
            archive.writestr('assets/ego/' + worker, value)
        archive.write(PROJECT / 'docs/official-rules.json', 'assets/docs/official-rules.json')

        # Reuse public default strategy thresholds. Runtime state and the
        # production credential directory are never copied into the artifact.
        cfg = json.loads((PROJECT / 'config.json').read_text('utf-8'))
        starter = {'accounts': [], 'legacy_root': 'legacy-import', 'poll_seconds': 60,
                   'rules': cfg['rules'],
                   'ai': {'enabled': False, 'endpoint': '', 'model': '', 'key_env': 'QIANCHUAN_AI_API_KEY'}}
        archive.writestr('assets/defaults/config.json', json.dumps(starter, ensure_ascii=False, indent=2))
        archive.writestr('assets/defaults/data/execution-strategy.json',
                         '{"mode":"api_control_browser_chase","version":1}')
    return seal(raw.getvalue(), key), imports


def icon_from_png(path, target):
    png = path.read_bytes()
    width, height = struct.unpack('>II', png[16:24])
    if width > 256 or height > 256:
        return None
    header = struct.pack('<HHHBBBBHHII', 0, 1, 1, width % 256, height % 256, 0, 0,
                         1, 32, len(png), 22)
    target.write_bytes(header + png)
    return target


def build_native(build, public_key, content_key):
    modules = ['qcx_client', 'qcx_payload', 'qcx_license', 'qcx_issuer',
               'qcx_client_config', 'qcx_issuer_config']
    for module in modules[:4]:
        shutil.copyfile(HERE / (module + '.py'), build / (module + '.py'))
    (build / 'qcx_client_config.py').write_text(
        f'PUBLIC_KEY=bytes.fromhex({public_key.hex()!r})\nCONTENT_KEY=bytes.fromhex({content_key.hex()!r})\n',
        'ascii')
    (build / 'qcx_issuer_config.py').write_text(f'PUBLIC_KEY=bytes.fromhex({public_key.hex()!r})\n', 'ascii')
    (build / 'setup.py').write_text(
        'from setuptools import setup\nfrom Cython.Build import cythonize\n'
        f'setup(ext_modules=cythonize({[m + ".py" for m in modules]!r}, '
        'compiler_directives={"language_level":3, "embedsignature":False}))\n',
        'utf-8')
    subprocess.run([sys.executable, 'setup.py', 'build_ext', '--inplace'], cwd=build, check=True)
    # Analysis must see only the native modules, otherwise PyInstaller will
    # collect an uncompiled copy of the loader or embed the build-time script.
    binaries = build / 'native'
    binaries.mkdir()
    for path in build.glob('qcx_*.pyd'):
        shutil.copy2(path, binaries)
    return binaries


def package_program(build, native, output, name, module, imports, *, payload=None, icon=None):
    launch = build / (name + '_boot.py')
    launch.write_text(f'from {module} import main\nraise SystemExit(main())\n', 'ascii')
    from os import environ
    env = dict(environ)
    env.pop('PYTHONPATH', None)
    env.pop('PYTHONHOME', None)
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed', '--onedir', '--noupx',
            '--exclude-module', 'monitor', '--paths', str(native), '--name', name,
            '--distpath', str(output), '--workpath', str(build / 'freeze'), '--specpath', str(build)]
    if icon:
        args.extend(['--icon', str(icon)])
    if payload is not None:
        args.extend(['--add-data', str(payload) + ':.'])
    for hidden in sorted(imports):
        if (hidden.split('.')[0] not in {'monitor', '__future__', '__main__'}):
            args.extend(['--hidden-import', hidden])
    # Running elsewhere avoids making the pre-compilation .py files fallback
    # import candidates. The generated bootstrap lives next to native code.
    local = native / launch.name
    shutil.move(launch, local)
    subprocess.run([*args, str(local)], cwd=native, env=env, check=True)
    return output / name


def copy_notices(folder):
    target = folder / '第三方许可'
    target.mkdir()
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copyfile(python_license, target / 'Python.txt')
    from importlib.metadata import distribution
    for package in ('pyinstaller', 'cryptography', 'cffi'):
        dist = distribution(package)
        for file in dist.files or []:
            if file.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
                source = dist.locate_file(file)
                if source.is_file():
                    output = target / (package + '-' + file.name)
                    if not output.exists():
                        shutil.copyfile(source, output)


def write_zip(folder, output, prefix):
    with zipfile.ZipFile(output, 'x', zipfile.ZIP_DEFLATED, compresslevel=7) as archive:
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                archive.write(path, prefix + '/' + path.relative_to(folder).as_posix())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--esbuild', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        parser.error('此构建需要 Windows x64。')
    esbuild = args.esbuild.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    names = [f'{CLIENT_NAME}-Windows-x64-{args.version}.zip', f'{OWNER_NAME}-Windows-x64-{args.version}.zip']
    if any((output / name).exists() for name in names):
        parser.error('目标版本已存在，请使用新的版本号或输出目录。')

    public_key = load_or_create_key().public_key().public_bytes_raw()
    print('签发公钥指纹', hashlib.sha256(public_key).hexdigest()[:24], flush=True)
    content_key = secrets.token_bytes(32)
    with tempfile.TemporaryDirectory(prefix='qcx-build-') as tmp:
        build = Path(tmp)
        frozen = build / 'frozen'
        payload = build / 'data.qcx'
        sealed, business_imports = build_payload(esbuild, content_key)
        payload.write_bytes(sealed)
        icon = icon_from_png(PROJECT / 'static/qianchuan-app-icon.png', build / 'app.ico')
        native = build_native(build, public_key, content_key)
        client_modules = ('qcx_client', 'qcx_license', 'qcx_payload', 'qcx_client_config')
        client_imports = set(business_imports) | set(client_modules)
        for m in client_modules[:3]:
            client_imports.update(code_imports(HERE / (m + '.py')))
        client = package_program(build, native, frozen, 'QianchuanCruise', 'qcx_client',
                                 client_imports, payload=payload, icon=icon)
        (client / 'QianchuanCruise.exe').rename(client / (CLIENT_NAME + '.exe'))
        shutil.copyfile(HERE / '客户使用说明.txt', client / '开始使用.txt')
        copy_notices(client)
        owner_modules = ('qcx_issuer', 'qcx_license', 'qcx_issuer_config')
        owner_imports = set(owner_modules)
        for m in owner_modules[:2]:
            owner_imports.update(code_imports(HERE / (m + '.py')))
        owner = package_program(build, native, frozen, 'QianchuanCruiseIssuer', 'qcx_issuer',
                                owner_imports, icon=icon)
        (owner / 'QianchuanCruiseIssuer.exe').rename(owner / (OWNER_NAME + '.exe'))
        shutil.copyfile(HERE / '签发人说明.txt', owner / '签发与密钥备份.txt')
        copy_notices(owner)
        for folder, name, prefix in zip((client, owner), names, (CLIENT_NAME, OWNER_NAME)):
            staged = build / name
            write_zip(folder, staged, prefix)
            shutil.copyfile(staged, output / name)
            print(output / name, flush=True)
    sums = [hashlib.sha256((output / name).read_bytes()).hexdigest() + '  ' + name for name in names]
    (output / f'SHA256-{args.version}.txt').write_text('\n'.join(sums) + '\n', 'utf-8')


if __name__ == '__main__':
    main()
