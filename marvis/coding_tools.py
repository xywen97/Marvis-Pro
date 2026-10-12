"""Coding tools: non-executing syntax checks and explicitly approved host commands."""
from __future__ import annotations

import ast
import json
import os
import selectors
import signal
import subprocess
import time


class CodingTools:
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
        return {'action': '执行代码命令', 'argv': list(args['argv']), 'cwd': str(cwd),
                'timeout': args.get('timeout', 60),
                'notice': '在真实电脑执行，没有隔离沙箱；进程继承当前环境和权限，可能读写授权目录以外的文件或访问网络。argv 按字面参数传入，不自动展开 shell 语法。'}

    def code_execute(self, args, plan):
        current = self.code_preview(args)
        if current != plan: raise PermissionError('执行参数与已确认预览不一致。')
        self.check()
        proc = subprocess.Popen(current['argv'], cwd=current['cwd'], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                shell=False, start_new_session=True)
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
                  'output': text[:20000], 'truncated': truncated or len(text) > 20000,
                  'timed_out': timed_out, 'elapsed_ms': round((time.monotonic() - started) * 1000)}
        if timed_out: result['error'] = '命令超时，已终止进程组。'
        elif proc.returncode: result['error'] = '命令退出码非零，请检查输出。'
        return result
