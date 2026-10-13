"""Coding tools: non-executing syntax checks and explicitly approved host commands."""
from __future__ import annotations

import ast
import json
import os
import selectors
import signal
import subprocess
import shutil
import time
from pathlib import Path


class CodingTools:
    def command_environment(self):
        # Desktop/runtime Python settings must not leak into unrelated interpreters.
        env = os.environ.copy()
        for key in ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', '__PYVENV_LAUNCHER__'):
            env.pop(key, None)
        return env

    def python_interpreter(self, cwd):
        for name in ('python3', 'python'):
            candidate = Path(cwd) / '.venv' / 'bin' / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate.absolute())
        return shutil.which('python3') or shutil.which('python')

    def command_argv(self, args):
        argv = list(args['argv'])
        if argv[0] in ('python', 'python3'):
            interpreter = self.python_interpreter(args['cwd'])
            if not interpreter: raise ValueError('找不到 Python 解释器。')
            argv[0] = interpreter
        elif '/' in argv[0]:
            argv[0] = os.path.abspath(os.path.join(args['cwd'], argv[0]))
        return argv

    def python_verification(self, argv, cwd):
        """Recognize only direct script/doctest checks, never -c or arbitrary modules."""
        executable = Path(argv[0]).name
        if executable not in ('python', 'python3') and not executable.startswith('python3.'):
            return None
        target = argv[1] if len(argv) == 2 else argv[3] if len(argv) == 4 and argv[1:3] == ['-m', 'doctest'] else None
        if not target or target.startswith('-'): return None
        try:
            path, _, _, digest = self.text_snapshot(str(Path(cwd) / target))
        except (ValueError, PermissionError, OSError): return None
        if path.suffix != '.py': return None
        return {'path': str(path), 'sha256': digest,
                'check': 'doctest' if len(argv) == 4 else 'script'}

    def code_check(self, args):
        p, _, text, fingerprint = self.text_snapshot(args['path'])
        language = {'.py': 'python', '.json': 'json'}.get(p.suffix.lower())
        if language is None:
            raise ValueError('静态语法检查仅支持 Python 和 JSON；其他语言使用项目检查命令。')
        result = {'path': str(p), 'language': language, 'sha256': fingerprint,
                  'valid': True, 'notice': '只检查语法，不执行代码，不证明行为正确。'}
        try:
            if language == 'python': ast.parse(text, filename=str(p))
            else: json.loads(text)
        except (SyntaxError, json.JSONDecodeError) as error:
            result.update(valid=False, error=str(error), line=error.lineno,
                          column=getattr(error, 'offset', getattr(error, 'colno', None)))
        return result

    def code_preview(self, args):
        if self.local: raise PermissionError('本地模式禁止代码命令执行，避免通过子进程联网。')
        cwd = self.path(args['cwd'])
        if not cwd.is_dir(): raise ValueError('命令工作目录必须存在。')
        argv = self.command_argv({**args, 'cwd': str(cwd)})
        verification = self.python_verification(argv, str(cwd))
        return {'action': '执行代码命令', 'argv': argv, 'cwd': str(cwd),
                'timeout': args.get('timeout', 60),
                'verification': verification,
                'environment': '移除继承的 PYTHONHOME/PYTHONPATH/VIRTUAL_ENV/__PYVENV_LAUNCHER__；裸 python 优先使用工作目录 .venv',
                'notice': '在真实电脑执行，没有隔离沙箱；进程继承当前环境和权限，可能读写授权目录以外的文件或访问网络。argv 按字面参数传入，不自动展开 shell 语法。'}

    def code_execute(self, args, plan):
        current = self.code_preview(args)
        if current != plan: raise PermissionError('执行参数与已确认预览不一致。')
        self.check()
        try:
            proc = subprocess.Popen(current['argv'], cwd=current['cwd'], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    shell=False, start_new_session=True, env=self.command_environment())
        except OSError as error:
            return {'argv': current['argv'], 'cwd': current['cwd'], 'exit_code': None,
                    'verification': current['verification'], 'error': '无法启动命令：' + str(error),
                    'output': str(error), 'truncated': False, 'timed_out': False, 'elapsed_ms': 0}
        selector = selectors.DefaultSelector()
        output = bytearray(); truncated = False; timed_out = False
        started = time.monotonic()
        try:
            os.set_blocking(proc.stdout.fileno(), False)
            selector.register(proc.stdout, selectors.EVENT_READ)
            while selector.get_map():
                self.check()
                if time.monotonic() - started >= current['timeout']:
                    timed_out = True
                    break
                for key, _ in selector.select(.05):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = max(0, 65536 - len(output))
                    output.extend(chunk[:remaining])
                    truncated |= len(chunk) > remaining
            # A process may close its output before it exits.
            while not timed_out and proc.poll() is None:
                self.check()
                timed_out = time.monotonic() - started >= current['timeout']
                if not timed_out: time.sleep(.05)
        finally:
            # Kill the process group even when the parent exited, to clean up children.
            try: os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            proc.wait()
            selector.close()
            proc.stdout.close()
        text = output.decode('utf-8', errors='replace')
        result = {'argv': current['argv'], 'cwd': current['cwd'], 'exit_code': proc.returncode,
                  'verification': current['verification'],
                  'output': text[:20000], 'truncated': truncated or len(text) > 20000,
                  'timed_out': timed_out, 'elapsed_ms': round((time.monotonic() - started) * 1000)}
        if timed_out: result['error'] = '命令超时，已终止进程组。'
        elif proc.returncode: result['error'] = '命令退出码非零，请检查输出。'
        if current['verification']:
            after = self.python_verification(current['argv'], current['cwd'])
            if after != current['verification']:
                result['error'] = '验证期间文件内容发生变化，结果不证明当前文件正确。'
        return result
