"""Explicit, short-lived permission to verify an approved Python artifact."""
from __future__ import annotations

import os
import hashlib
import time
from pathlib import Path


class TaskAuthorization:
    def __init__(self):
        self.verifications = []

    @staticmethod
    def offer(caps, name, plan, args):
        if caps.local or name not in ('file_write', 'file_edit'): return None
        path = Path(plan['path'])
        if path.suffix != '.py': return None
        interpreter = caps.python_interpreter(path.parent)
        if not interpreter: return None
        try: identity = list(TaskAuthorization.interpreter_identity(interpreter))
        except OSError: return None
        text = args['text'] if name == 'file_write' else caps.text_snapshot(str(path))[2].replace(
            args['old_text'], args['new_text'], -1 if args.get('replace_all') else 1)
        return {'path': str(path), 'cwd': str(path.parent), 'interpreter': interpreter,
                'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
                'interpreter_identity': identity,
                'expires_seconds': 900, 'max_timeout': 60,
                'description': '确认本次文件修改，并授权本任务在 15 分钟内运行此版本的 Python 文件及其 doctest（每次最多 60 秒）。不授权后续修改或其他命令；代码在真实电脑执行，可访问文件和网络。'}

    @staticmethod
    def interpreter_identity(path):
        st = os.stat(path)
        return (str(Path(path).resolve()), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)

    def grant(self, caps, offer, approved_at):
        try:
            _, _, _, digest = caps.text_snapshot(offer['path'])
            identity = self.interpreter_identity(offer['interpreter'])
        except (OSError, PermissionError, ValueError): return False
        if digest != offer['sha256']: return False
        if list(identity) != offer['interpreter_identity']: return False
        self.verifications.append({**offer,
                                   'expires_at': approved_at + offer['expires_seconds'],
                                   'interpreter_identity': identity})
        return True

    def allows(self, name, plan):
        if name != 'code_execute' or not plan.get('verification'): return False
        check = plan['verification']
        for grant in self.verifications:
            if (time.monotonic() >= grant['expires_at'] or plan['timeout'] > grant['max_timeout']
                    or plan['cwd'] != grant['cwd'] or check['path'] != grant['path']
                    or check['sha256'] != grant['sha256']): continue
            try:
                if os.path.abspath(plan['argv'][0]) != os.path.abspath(grant['interpreter']): continue
                if self.interpreter_identity(plan['argv'][0]) != grant['interpreter_identity']: continue
            except OSError: continue
            return True
        return False
