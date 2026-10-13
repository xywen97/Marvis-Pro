#!/usr/bin/env python3
"""Build a relocatable macOS App and TUI with native Python/OpenViking included.

Build tools and downloads are required only on the build machine. Nothing is
installed or downloaded at application startup. No developer secrets are copied.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL_NAME = 'bge-small-zh-v1.5-f16.gguf'
MODEL_SHA256 = 'ab9b81d9cd329c712eee379cf0068eabe6a5e2a01d0def61535eba9384085e2c'
MODEL_URL = 'https://huggingface.co/CompendiumLabs/bge-small-zh-v1.5-gguf/resolve/main/' + MODEL_NAME + '?download=true'


def model_asset(path):
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
        path.unlink()
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.part')
        request = urllib.request.Request(MODEL_URL + '&marvis_verified=1', headers={'User-Agent': 'MarvisXY-build'})
        with urllib.request.urlopen(request, timeout=60) as source, temporary.open('wb') as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != MODEL_SHA256:
            temporary.unlink()
            raise RuntimeError('嵌入模型下载校验失败，请重试构建。')
        temporary.replace(path)
    with path.open('rb') as file:
        if file.read(4) != b'GGUF': raise RuntimeError('嵌入模型不是有效 GGUF 文件。')
    return path


def copy_runtime(destination, python):
    info = json.loads(subprocess.check_output([str(python), '-c',
        'import sys,json; print(json.dumps({"prefix":sys.base_prefix,"version":f"{sys.version_info.major}.{sys.version_info.minor}"}))'], text=True))
    base = Path(info['prefix'])
    def ignore(directory, names):
        return [name for name in names if name in ('site-packages', '__pycache__', 'test', 'tests', 'idlelib', 'tkinter', 'ensurepip') or name.endswith('.pyc')]
    shutil.copytree(base, destination, ignore=ignore, symlinks=False, ignore_dangling_symlinks=True)
    # Console scripts from the source installation can contain absolute shebangs;
    # launch only our wrappers and the bundled interpreter.
    for path in (destination / 'bin').iterdir():
        if path.name not in ('python', 'python3', 'python' + info['version']):
            if path.is_dir(): shutil.rmtree(path)
            else: path.unlink()
    packages = destination / 'lib' / ('python' + info['version']) / 'site-packages'
    packages.mkdir(parents=True, exist_ok=True)
    return destination / 'bin' / ('python' + info['version']), packages


def sanitized_config():
    import yaml
    config = yaml.safe_load((ROOT / 'config.yaml').read_text()) or {}
    config['llm'] = {'provider': 'openai', 'base_url': '', 'model': '', 'api_key': '', 'temperature': .2}
    config.setdefault('memory', {}).update(enabled=True, deployment='managed')
    config['memory'].pop('url', None)
    config['memory']['managed'] = {'startup_timeout': 90, 'max_restarts': 3}
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)


def build(output, python, model, install=True):
    if sys.platform != 'darwin': raise RuntimeError('请在目标 macOS 架构上构建 App。')
    model = model_asset(model)
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='marvis-native-', dir=ROOT / 'build'))
    try:
        app = staging / 'MarvisXY.app'
        contents = app / 'Contents'
        resources = contents / 'Resources'
        source = resources / 'app'
        source.mkdir(parents=True)
        executable, packages = copy_runtime(resources / 'python', python)
        if install:
            subprocess.run(['uv', 'pip', 'install', '--python', str(executable), '--target', str(packages),
                            '-r', str(ROOT / 'requirements-bundle.lock')], check=True)
        else:
            # Developer optimization: copy a separately resolved locked build
            # environment. Never copy the user's project .venv or configuration.
            info = subprocess.check_output([str(python), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
            shutil.copytree(info, packages, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'), symlinks=False)
        for name in ('marvis', 'agents', 'skills', 'tools', 'ui'):
            shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
        for name in ('main.py', 'agent.py', 'llm_client.py'):
            shutil.copy2(ROOT / name, source / name)
        (source / 'config.yaml').write_text(sanitized_config())
        (source / 'models').mkdir()
        shutil.copy2(model, source / 'models' / MODEL_NAME)
        digest = hashlib.sha256(model.read_bytes()).hexdigest()
        manifest = {'app_version': '0.2.0', 'openviking_version': '0.5.0', 'architecture': platform.machine(),
                    'embedding_model': MODEL_NAME, 'embedding_sha256': digest}
        (source / 'bundle.json').write_text(json.dumps(manifest, indent=2) + '\n')
        licenses = resources / 'licenses'; licenses.mkdir()
        (licenses / 'THIRD_PARTY.md').write_text('OpenViking 0.5.0: AGPL-3.0\nSource: https://github.com/volcengine/OpenViking/tree/v0.5.0\n\nBundled Python package licenses are retained in their .dist-info directories.\nEmbedding model: BAAI/bge-small-zh-v1.5 (MIT), GGUF by CompendiumLabs.\nhttps://huggingface.co/BAAI/bge-small-zh-v1.5\n')
        shutil.copy2(ROOT / 'requirements-bundle.lock', resources / 'requirements-bundle.lock')
        with (resources / 'installed-packages.json').open('w') as file:
            subprocess.run([str(executable), '-c', 'import importlib.metadata as m,json; print(json.dumps(sorted([{"name":d.metadata["Name"],"version":d.version} for d in m.distributions()],key=lambda d:d["name"].lower())))'], stdout=file, check=True)
        macos = contents / 'MacOS'; macos.mkdir()
        # Cocoa derives notification identity from the executable's .app bundle.
        # Running the interpreter under Resources/python/bin loses that identity.
        shutil.copy2(executable, macos / 'MarvisXY-python')
        relative_python = '../Resources/python/bin/' + executable.name
        version = executable.name.removeprefix('python')
        common = '#!/bin/sh\nset -eu\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nunset PYTHONPATH PYTHONHOME\nexport PYTHONNOUSERSITE=1\nexport SSL_CERT_FILE="${SSL_CERT_FILE:-$HERE/../Resources/python/lib/python' + version + '/site-packages/certifi/cacert.pem}"\nexec "$HERE/' + relative_python + '" "$HERE/../Resources/app/main.py" '
        for name, mode in [('MarvisXY', '--app'), ('marvis', '')]:
            launcher = macos / name
            desktop_common = common.replace('unset PYTHONPATH PYTHONHOME',
                'unset PYTHONPATH\nexport PYTHONHOME="$HERE/../Resources/python"').replace(
                'exec "$HERE/' + relative_python + '"', 'exec "$HERE/MarvisXY-python"')
            launcher.write_text((desktop_common if name == 'MarvisXY' else common) + mode + ' "$@"\n'); launcher.chmod(0o755)
        plist = {'CFBundleName': 'MarvisXY', 'CFBundleDisplayName': 'MarvisXY', 'CFBundleIdentifier': 'local.marvisxy.desktop',
                 'CFBundleVersion': '0.2.0', 'CFBundleShortVersionString': '0.2.0', 'CFBundleExecutable': 'MarvisXY',
                 'CFBundlePackageType': 'APPL', 'NSHighResolutionCapable': True,
                 'LSMinimumSystemVersion': '14.0' if platform.machine() == 'arm64' else '15.0'}
        with (contents / 'Info.plist').open('wb') as file: plistlib.dump(plist, file)
        # Verify in a new location, isolated from the checkout and user config.
        check = staging / 'relocated' / 'MarvisXY.app'; check.parent.mkdir(); app.rename(check)
        with tempfile.TemporaryDirectory() as user_home:
            env = {**os.environ, 'HOME': user_home, 'PYTHONNOUSERSITE': '1'}
            env.pop('PYTHONPATH', None); env.pop('PYTHONHOME', None)
            command = check / 'Contents' / 'MacOS' / 'marvis'
            subprocess.run([str(command), '--diagnose-bundle'], cwd=user_home, env=env, check=True)
            subprocess.run([str(command), '--offline', '--data-dir', str(Path(user_home) / 'smoke-data'), '/tool agent_inspect {}'],
                           cwd=user_home, env=env, stdout=subprocess.DEVNULL, check=True)
            endpoint = Path(user_home) / 'smoke-data' / 'endpoint.json'
            if endpoint.exists():
                pid = json.loads(endpoint.read_text())['pid']
                os.kill(pid, signal.SIGTERM)
                deadline = time.monotonic() + 10
                while endpoint.exists() and time.monotonic() < deadline: time.sleep(.1)
                if endpoint.exists(): raise RuntimeError('独立引擎未能在构建验证后退出。')
            # Exercise the actual native embedding library and HTTP service after
            # relocation, using bundled components and no developer credentials.
            checked_source = check / 'Contents' / 'Resources' / 'app'
            checked_python = check / 'Contents' / 'Resources' / 'python' / 'bin' / executable.name
            smoke = '''import sys,time,tempfile
sys.path.insert(0,sys.argv[1])
from marvis.runtime import Runtime
with tempfile.TemporaryDirectory() as d:
 r=Runtime(d,{'llm':{'env_file':'/nonexistent'},'memory':{'enabled':True,'deployment':'managed','managed':{'startup_timeout':45,'max_restarts':0}}})
 try:
  end=time.monotonic()+50
  while time.monotonic()<end and r.memory.service.status()['state'] not in ('ready','failed'):time.sleep(.1)
  assert r.memory.service.status()['state']=='ready',r.memory.service.status()
  print('Relocated native OpenViking retrieval verified')
 finally:r.close()
'''
            clean_env = {key: value for key, value in env.items() if not key.startswith(('OPENAI_', 'OPENVIKING_', 'SSL_CERT_FILE'))}
            subprocess.run([str(checked_python), '-c', smoke, str(checked_source)], cwd=user_home, env=clean_env, check=True, timeout=70)
        app = check
        destination = output / 'MarvisXY.app'
        if destination.exists():
            backup = output / 'MarvisXY.previous.app'
            if backup.exists(): shutil.rmtree(backup)
            destination.rename(backup)
        app.rename(destination)
        tui = output / 'marvis'
        tui.write_text('#!/bin/sh\nset -eu\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$HERE/MarvisXY.app/Contents/MacOS/marvis" "$@"\n')
        tui.chmod(0o755)
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent', str(destination), str(output / ('MarvisXY-macOS-' + platform.machine() + '.zip'))], check=True)
        print('Built:', destination)
        print('TUI:', tui)
        return destination
    finally:
        shutil.rmtree(staging)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / 'native')
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    parser.add_argument('--model', type=Path, default=ROOT / '.cache' / 'openviking' / 'models' / MODEL_NAME)
    parser.add_argument('--resolved-environment', action='store_true', help='Copy dependencies from --python environment, already synced to requirements-bundle.lock')
    args = parser.parse_args()
    (ROOT / 'build').mkdir(exist_ok=True)
    build(args.output.resolve(), args.python.absolute(), args.model.resolve(), install=not args.resolved_environment)


if __name__ == '__main__': main()
