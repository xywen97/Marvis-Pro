import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import urlsplit

from marvis.client import connect, RemoteRuntime
from marvis.server import serve


class EngineProcessTests(unittest.TestCase):
    def test_clients_share_daemon_and_last_disconnect_stops_it(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(__file__).with_name('engine_fixture.py')
            process = subprocess.Popen([sys.executable, str(fixture), directory], cwd=fixture.parents[1], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            first = second = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    first = connect(directory)
                    if first: break
                    if process.poll() is not None: self.fail(process.stderr.read().decode())
                    time.sleep(.03)
                self.assertIsNotNone(first)
                first.attach()
                second = connect(directory); second.attach()
                endpoint = json.loads((Path(directory) / 'endpoint.json').read_text())
                self.assertEqual(endpoint['pid'], process.pid)
                first.close()
                time.sleep(1.3)
                self.assertIsNone(process.poll())
                self.assertTrue(second.settings()['offline'])
                sid = second.new_session()
                # Desktop's local UI proxy must forward slash commands to the
                # shared engine, including commands that need the real Store.
                proxy, url = serve(second)
                try:
                    parsed = urlsplit(url)
                    client = RemoteRuntime({'base': f'{parsed.scheme}://{parsed.netloc}', 'token': parsed.fragment.removeprefix('token=')})
                    self.assertIn('/help', client.command('/help', sid)['text'])
                    self.assertEqual(client.snapshot()['tasks'], [])
                finally: proxy.shutdown(); proxy.server_close()
                second.close()
                process.wait(timeout=5)
                self.assertEqual(process.returncode, 0)
                self.assertFalse((Path(directory) / 'endpoint.json').exists())
            finally:
                if first: first.close()
                if second: second.close()
                if process.poll() is None: process.terminate(); process.wait(timeout=5)
                process.stderr.close()


if __name__ == '__main__': unittest.main()
