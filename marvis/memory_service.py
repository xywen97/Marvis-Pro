"""Own and supervise a native OpenViking subprocess, never a global daemon."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from marvis.paths import ROOT, bundled


class NativeMemoryService:
    def __init__(self, memory, options, env):
        self.memory = memory
        self.runtime = memory.runtime
        self.options = options
        self.env = env
        self.directory = self.runtime.store.directory / 'openviking'
        self.stop = threading.Event()
        self.changed = threading.Event()
        self.lock = threading.Lock()
        self.state = 'starting'
        self.error = ''
        self.process = None
        self.starts = 0
        self.generation = 0
        self.thread = threading.Thread(target=self._supervise, daemon=True, name='marvis-openviking')
        self.thread.start()

    def status(self):
        with self.lock:
            return {'deployment': 'managed', 'state': self.state, 'error': self.error,
                    'starts': self.starts, 'pid': self.process.pid if self.process and self.process.poll() is None else None}

    def _set(self, state, error=''):
        with self.lock: self.state, self.error = state, error

    def reconfigure(self):
        self.generation += 1
        self.changed.set()

    def _python(self):
        configured = self.options.get('python')
        if configured:
            path = Path(configured).expanduser().absolute()
            if not path.is_file(): raise RuntimeError('配置的 OpenViking Python 不存在。')
            return str(path)
        if importlib.util.find_spec('openviking_cli') is not None:
            return sys.executable
        candidate = ROOT / '.venv-openviking' / 'bin' / 'python'
        if not bundled() and candidate.is_file(): return str(candidate)
        raise RuntimeError('记忆服务运行组件缺失；开发环境请安装 requirements-memory.txt，发布包请重新构建。')

    def _prepare(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path = self.directory / 'service.key'
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as file: file.write(secrets.token_urlsafe(32))
        except FileExistsError: pass
        key = key_path.read_text().strip()
        if not key: raise RuntimeError('记忆服务密钥文件为空。')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        model = self.runtime.model(self.runtime.settings())
        embedding = {'provider': 'local', 'model': 'bge-small-zh-v1.5-f16',
                     'cache_dir': str(self.directory / 'models')}
        model_path = self.options.get('embedding_model_path') or ROOT / 'models' / 'bge-small-zh-v1.5-f16.gguf'
        if not Path(model_path).is_file() and not self.options.get('embedding_model_path') and not bundled():
            model_path = ROOT / '.cache' / 'openviking' / 'models' / 'bge-small-zh-v1.5-f16.gguf'
        if Path(model_path).expanduser().is_file():
            embedding['model_path'] = str(Path(model_path).expanduser().resolve())
        elif bundled():
            raise RuntimeError('发布包缺少本地嵌入模型，请重新构建。')
        vlm = {}
        if model.configured:
            vlm = {'provider': 'openai', 'api_base': model.base_url, 'model': model.model,
                   'api_key': '${MARVIS_MEMORY_MODEL_KEY}', 'temperature': 0}
        config = {'storage': {'workspace': str(self.directory / 'data')},
                  'server': {'host': '127.0.0.1', 'port': port, 'auth_mode': 'trusted',
                             'root_api_key': '${MARVIS_MEMORY_SERVICE_KEY}'},
                  'embedding': {'dense': embedding}, 'vlm': vlm}
        target = self.directory / 'ov.conf'
        temporary = self.directory / 'ov.conf.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as file: json.dump(config, file, ensure_ascii=False)
        temporary.replace(target)
        child_env = os.environ.copy()
        child_env.update(MARVIS_MEMORY_SERVICE_KEY=key, MARVIS_MEMORY_MODEL_KEY=model.api_key,
                         OPENVIKING_CONFIG_FILE=str(target), OPENVIKING_WITH_BOT='0',
                         PYTHONUNBUFFERED='1')
        # Never inherit server overrides from an unrelated external deployment.
        for name in ('OPENVIKING_SERVER_PORT', 'OPENVIKING_SERVER_HOST', 'PYTHONPATH', 'PYTHONHOME'):
            child_env.pop(name, None)
        url = f'http://127.0.0.1:{port}'
        from marvis.memory import OpenVikingClient
        self.memory.client = OpenVikingClient({**self.options, 'url': url, 'auth_mode': 'trusted'},
                                             {'OPENVIKING_API_KEY': key})
        return target, child_env, url

    def _ready(self, url):
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url + '/ready', timeout=.5) as response:
                result = json.load(response)
            if result.get('status') != 'ready': return False
            # Authenticated request proves this is our child, not another process
            # that happened to claim the chosen loopback port.
            self.memory.client.request('GET', '/api/v1/sessions')
            from marvis.memory import MemoryError
            try:
                self.memory.client.request('POST', '/api/v1/sessions', {'session_id': 'marvis-bootstrap', 'auto_commit_policy': None})
            except MemoryError as error:
                if error.http_status != 409: raise
            found = self.memory.client.request('POST', '/api/v1/search/find', {
                'query': 'memory service readiness', 'target_uri': 'viking://~/memories',
                'context_type': 'memory', 'limit': 1})
            return isinstance(found, dict) and isinstance(found.get('memories'), list)
        except (OSError, ValueError, RuntimeError): return False

    def _terminate(self):
        process = self.process
        if process is None: return
        if process.stdin:
            try: process.stdin.close()
            except OSError: pass
        if process.poll() is None:
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=5)
        self.process = None

    def _supervise(self):
        attempts = 0
        while not self.stop.is_set():
            generation = self.generation
            self.changed.clear()
            try:
                self._set('starting' if attempts == 0 else 'restarting')
                python = self._python()
                config, env, url = self._prepare()
                log = self.directory / 'service.log'
                # Keep diagnostics private and bound their size between starts.
                if log.exists() and log.stat().st_size > 2_000_000: log.unlink()
                fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, 'ab') as output:
                    self.process = subprocess.Popen([python, '-m', 'marvis.memory_child', '--config', str(config)],
                        cwd=ROOT, env=env, stdin=subprocess.PIPE, stdout=output, stderr=output,
                        start_new_session=True)
                self.starts += 1
                deadline = time.monotonic() + self.options.get('startup_timeout', 90)
                while not self.stop.is_set() and generation == self.generation:
                    if self.process.poll() is not None:
                        raise RuntimeError('记忆服务启动或运行失败，详见记忆服务日志。')
                    if self._ready(url): break
                    if time.monotonic() >= deadline: raise RuntimeError('记忆服务启动超时，详见记忆服务日志。')
                    self.stop.wait(.2)
                if self.stop.is_set(): break
                if generation != self.generation: continue
                self._set('ready')
                self.memory.wake.set()
                while not self.stop.is_set() and generation == self.generation:
                    if self.process.poll() is not None: raise RuntimeError('记忆服务异常退出，正在尝试恢复。')
                    self.changed.wait(.5)
            except (OSError, RuntimeError, ValueError) as error:
                # Our own messages contain no request bodies, keys or provider errors.
                message = str(error) if isinstance(error, RuntimeError) else '记忆服务无法启动，详见记忆服务日志。'
                self._set('restarting', message)
                attempts += 1
            finally:
                self._terminate()
            if generation != self.generation:
                attempts = 0
                continue
            if attempts >= self.options.get('max_restarts', 3) + 1:
                self._set('failed', self.error)
                self.changed.wait()
                attempts = 0
            else:
                self.changed.wait(min(2 ** attempts, 10))
        self._set('stopped')

    def close(self):
        self.stop.set(); self.changed.set()
        self.thread.join()
