import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from marvis.runtime import Runtime
from marvis.client import connect
from marvis.server import serve
from marvis.tui import Terminal


class NativeMemoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = {'llm': {'env_file': str(self.root / '.env')}, 'memory': {
            'enabled': True, 'deployment': 'managed', 'managed': {'python': sys.executable, 'startup_timeout': 3, 'max_restarts': 1}}}
        self.real_popen = subprocess.Popen
        self.processes = []
        fixture = str(Path(__file__).with_name('memory_service_fixture.py'))
        def spawn(args, **kwargs):
            process = self.real_popen([sys.executable, fixture, args[-1]], **kwargs)
            self.processes.append(process)
            return process
        self.spawn_patch = patch('marvis.memory_service.subprocess.Popen', side_effect=spawn)
        self.spawn_patch.start()
        self.runtime = Runtime(self.root / 'data', self.config)

    def tearDown(self):
        self.runtime.close(); self.spawn_patch.stop()
        for process in self.processes:
            if process.poll() is None: process.kill(); process.wait()
        self.tmp.cleanup()

    def wait_ready(self, starts=1):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            status = self.runtime.memory.service.status()
            if status['state'] == 'ready' and status['starts'] >= starts: return status
            time.sleep(.03)
        self.fail(str(status))

    def test_automatic_start_shared_client_and_shutdown(self):
        status = self.wait_ready()
        self.assertEqual(status['starts'], 1)
        self.assertTrue(self.runtime.memory.client.url.startswith('http://127.0.0.1:'))
        server, _ = serve(self.runtime)
        try:
            attached = connect(self.root / 'data')
            self.assertFalse(attached.owned)
            self.assertEqual(attached.snapshot()['memory']['service']['pid'], status['pid'])
            attached.close()
            self.assertIsNone(self.processes[0].poll())
            with self.assertRaises(RuntimeError): Runtime(self.root / 'data', self.config)
        finally: server.shutdown(); server.server_close()
        self.runtime.close()
        self.assertIsNotNone(self.processes[0].poll())
        self.assertEqual(self.runtime.memory.service.status()['state'], 'stopped')

    def test_crash_restarts_with_same_private_key_and_storage(self):
        first = self.wait_ready()
        key = (self.root / 'data/openviking/service.key').read_text()
        os.kill(first['pid'], signal.SIGKILL)
        second = self.wait_ready(starts=2)
        self.assertNotEqual(first['pid'], second['pid'])
        self.assertEqual((self.root / 'data/openviking/service.key').read_text(), key)
        os.kill(second['pid'], signal.SIGKILL)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.runtime.memory.service.status()['state'] != 'failed': time.sleep(.03)
        self.assertEqual(self.runtime.memory.service.status()['state'], 'failed')

    def test_model_key_save_restarts_service_and_never_leaks_in_snapshot(self):
        self.wait_ready()
        self.runtime.update_settings({'model': {'base_url': 'https://model.example/v1', 'model': 'chat', 'api_key': 'private-test-key'}})
        self.wait_ready(starts=2)
        self.assertEqual((self.root / '.env').stat().st_mode & 0o777, 0o600)
        self.assertIn('private-test-key', (self.root / '.env').read_text())
        self.assertNotIn('private-test-key', json.dumps(self.runtime.snapshot()))
        config = json.loads((self.root / 'data/openviking/ov.conf').read_text())
        self.assertEqual(config['vlm']['api_key'], '${MARVIS_MEMORY_MODEL_KEY}')
        self.assertNotIn('private-test-key', json.dumps(config))
        self.assertEqual(self.runtime.model(self.runtime.settings()).api_key, 'private-test-key')

    def test_offline_starts_no_child(self):
        self.wait_ready(); self.runtime.close()
        before = len(self.processes)
        self.runtime = Runtime(self.root / 'data', self.config, offline=True)
        self.assertIsNone(self.runtime.memory.service)
        self.assertEqual(len(self.processes), before)

    def test_memory_toggle_stops_service_preserves_data_and_survives_restart(self):
        self.wait_ready()
        key = (self.root / 'data/openviking/service.key').read_text()
        self.runtime.memory.enqueue('retained', 'session', self.runtime.settings())
        with self.assertRaises(ValueError): self.runtime.update_settings({'memory_enabled': 'false'})
        self.runtime.update_settings({'memory_enabled': False})
        self.assertIsNotNone(self.processes[0].poll())
        self.assertFalse(self.runtime.settings()['memory_enabled'])
        self.assertFalse(self.runtime.memory.status()['active'])
        self.assertIsNone(self.runtime.memory.service)
        self.assertEqual(self.runtime.memory.recall('unused', 'query', self.runtime.settings()), [])
        self.runtime.memory.enqueue('disabled-turn', 'session', self.runtime.settings())
        self.assertEqual(self.runtime.store.one('SELECT count(*) AS n FROM memory_outbox')['n'], 1)
        self.runtime.close()
        before = len(self.processes)
        self.runtime = Runtime(self.root / 'data', self.config)
        self.assertFalse(self.runtime.memory.enabled)
        self.assertEqual(len(self.processes), before)
        self.runtime.update_settings({'memory_enabled': True})
        self.wait_ready()
        self.assertTrue(self.runtime.settings()['memory_enabled'])
        self.assertEqual((self.root / 'data/openviking/service.key').read_text(), key)
        self.assertEqual(self.runtime.store.one('SELECT count(*) AS n FROM memory_outbox')['n'], 1)

    def test_tui_secret_input_does_not_submit_a_task(self):
        self.wait_ready()
        self.runtime.update_settings({'model': {'base_url': 'https://model.example/v1', 'model': 'chat'}})
        terminal = Terminal(self.runtime)
        terminal.command('/key')
        for char in 'secret-typed': terminal.key_input(char)
        self.assertTrue(terminal.key_entry)
        self.assertEqual(terminal.suggestions(), [])
        terminal.key_input('\n')
        self.assertFalse(terminal.key_entry)
        self.assertEqual(terminal.input, '')
        self.assertEqual(self.runtime.snapshot()['tasks'], [])
        self.assertNotIn('secret-typed', json.dumps(self.runtime.snapshot()))
        self.assertIn('secret-typed', (self.root / '.env').read_text())
        with self.assertRaises(ValueError): terminal.command('/key plain-key')


if __name__ == '__main__': unittest.main()
