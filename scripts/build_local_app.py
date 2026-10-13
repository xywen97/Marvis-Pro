"""Build a double-clickable macOS development app pointing to this checkout."""
import os
import plistlib
import shlex
import shutil
import subprocess
import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
app = root / 'dist/MarvisXY.app'
contents = app / 'Contents'
macos = contents / 'MacOS'
macos.mkdir(parents=True, exist_ok=True)
python = root / '.venv/bin/python'
if not python.exists(): raise SystemExit('请先创建 .venv 并安装 requirements-desktop.txt。')
plist = {'CFBundleName':'MarvisXY', 'CFBundleDisplayName':'MarvisXY', 'CFBundleIdentifier':'local.marvisxy.desktop', 'CFBundleVersion':'0.2.0', 'CFBundleShortVersionString':'0.2.0', 'CFBundleExecutable':'MarvisXY', 'CFBundlePackageType':'APPL', 'NSHighResolutionCapable':True, 'LSMinimumSystemVersion':'13.0'}
with (contents / 'Info.plist').open('wb') as file: plistlib.dump(plist, file)
launcher = macos / 'MarvisXY'
info = json.loads(subprocess.check_output([str(python), '-c',
    'import sys,sysconfig,json; print(json.dumps({"prefix":sys.base_prefix,"packages":sysconfig.get_path("purelib")}))'], text=True))
shutil.copy2(python.resolve(), macos / 'MarvisXY-python')
launcher.write_text('#!/bin/sh\ncd ' + shlex.quote(str(root)) +
    '\nexport PYTHONHOME=' + shlex.quote(info['prefix']) +
    '\nexport PYTHONPATH=' + shlex.quote(info['packages']) +
    '\nexport PYTHONNOUSERSITE=1\nHERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)' +
    '\nexec "$HERE/MarvisXY-python" ' + shlex.quote(str(root / 'main.py')) + ' --app "$@"\n',encoding='utf-8')
launcher.chmod(0o755)
print(app)
print('本地开发 App；依赖当前项目目录与 .venv，不是独立分发包。')
