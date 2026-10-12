"""Shared packaged engine with client leases and automatic idle shutdown."""
from __future__ import annotations
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from marvis.paths import ROOT


class ClientLeases:
    def __init__(self, ttl=20):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.clients = {}
        self.last_active = time.monotonic()

    def update(self, ident, release=False):
        if not isinstance(ident, str) or not 1 <= len(ident) <= 64: raise ValueError('客户端 ID 无效。')
        with self.lock:
            if release: self.clients.pop(ident, None)
            else: self.clients[ident] = time.monotonic()
            self.last_active = time.monotonic()
        return {'managed': True}

    def idle(self, seconds):
        with self.lock:
            now = time.monotonic()
            self.clients = {key: seen for key, seen in self.clients.items() if now - seen < self.ttl}
            if self.clients:
                self.last_active = now
                return False
            return now - self.last_active >= seconds


def run_engine(runtime, idle_seconds=10):
    from marvis.server import serve
    runtime.client_leases = ClientLeases()
    server, _ = serve(runtime)
    stop = threading.Event()
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, lambda *_: stop.set())
    try:
        while not stop.wait(.5):
            busy = runtime.store.one("SELECT id FROM tasks WHERE status IN ('queued','running','waiting_approval')")
            workflows = runtime.store.one('SELECT id FROM workflows WHERE enabled=1')
            if busy or workflows:
                runtime.client_leases.last_active = time.monotonic()
                continue
            if runtime.client_leases.idle(idle_seconds): break
    finally:
        server.shutdown(); server.server_close()
        for sig, handler in previous.items(): signal.signal(sig, handler)


def start_engine(directory, config, offline=False):
    from marvis.client import connect
    client = connect(directory)
    if client: return client
    directory = Path(directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    args = [sys.executable, str(ROOT / 'main.py'), '--engine', '--data-dir', str(directory), '--config', str(config)]
    if offline: args.append('--offline')
    env = os.environ.copy()
    env.pop('PYTHONPATH', None); env.pop('PYTHONHOME', None)
    fd = os.open(directory / 'engine.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, 'ab') as log:
        process = subprocess.Popen(args, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        client = connect(directory)
        if client: return client
        # Another concurrent launcher may own the engine lock. Keep looking for
        # its endpoint even when the process we started exits on that lock.
        time.sleep(.05)
    if process.poll() is None:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
    raise RuntimeError('后台引擎未能启动，详见用户数据目录的 engine.log。')
