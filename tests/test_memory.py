import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from marvis.runtime import Runtime
from marvis.memory import MemoryError, OpenVikingClient
from llm_client import ChatResult, LLMClient
from scripts.setup_openviking import configure
from llm_client import load_env_file


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        self.messages = {}
        self.commits = {}
        self.sessions = set()
        self.remote_status = 'running'
        self.fail_batch_response = False
        self.fail_commit_response = False
        self.fail_search = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self): self.handle_request()
            def do_POST(self): self.handle_request()
            def handle_request(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length']))) if self.headers.get('Content-Length') else None
                owner.calls.append((self.command, self.path, body, dict(self.headers)))
                code = 200
                if self.path == '/api/v1/sessions':
                    result = {'session_id': body['session_id']}
                    if body['session_id'] in owner.sessions: code = 409
                    owner.sessions.add(body['session_id'])
                elif self.path == '/api/v1/search/find':
                    if owner.fail_search: code = 503
                    result = {'memories': [{'uri': 'viking://user/marvisxy/memories/preferences/style.md', 'abstract': '用户喜欢简洁中文。'}]}
                elif self.path.startswith('/api/v1/tasks?'):
                    result = list(owner.commits.values())[-1:]
                elif self.path.startswith('/api/v1/tasks/'):
                    result = {'status': owner.remote_status}
                else:
                    sid = self.path.split('/')[4]
                    if self.path.endswith('/messages/batch'):
                        owner.messages[sid] = body['messages']
                        result = {'message_count': len(body['messages'])}
                        if owner.fail_batch_response:
                            owner.fail_batch_response = False; code = 503
                    elif self.path.endswith('/commit'):
                        result = {'task_id': 'extract-' + sid}
                        owner.commits[sid] = result
                        if owner.fail_commit_response:
                            owner.fail_commit_response = False; code = 503
                    else:
                        result = {'total_message_count': len(owner.messages.get(sid, []))}
                self.send_response(code); self.send_header('Content-Type', 'application/json'); self.end_headers()
                self.wfile.write(json.dumps({'status': 'ok', 'result': result}).encode())

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.config = {'llm': {'env_file': '/nonexistent'}, 'memory': {'enabled': True, 'url': f'http://127.0.0.1:{self.server.server_port}', 'retry_seconds': 3600}}
        self.env = patch.dict('os.environ', {'OPENVIKING_API_KEY': 'fixture-key', 'OPENVIKING_URL': ''}); self.env.start()
        self.runtime = Runtime(self.tmp.name, config=self.config)
        self.runtime.memory.close()  # Drive delivery synchronously for deterministic checks.
        self.sid = self.runtime.new_session()
        self.ident = 'turn1'
        self.runtime.store.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)', (self.ident, self.sid, '偏好', 'completed', '收到', 1, 1))
        self.runtime.store.add_message(self.sid, 'user', '以后请用中文', self.ident)
        self.runtime.store.add_message(self.sid, 'assistant', '收到', self.ident)

    def tearDown(self):
        self.runtime.close(); self.env.stop(); self.server.shutdown(); self.server.server_close(); self.tmp.cleanup()

    def enqueue(self):
        self.runtime.memory.enqueue(self.ident, self.sid, self.runtime.settings())

    def test_extract_poll_restart_and_recall(self):
        self.enqueue(); self.runtime.memory.flush_once()
        self.assertEqual(self.runtime.memory.status()['deliveries'], {'extracting': 1})
        self.assertFalse(any(e['kind'] == 'memory_saved' for e in self.runtime.store.events(self.ident)))
        self.runtime.close()
        self.runtime = Runtime(self.tmp.name, config=self.config); self.runtime.memory.close()
        self.remote_status = 'completed'; self.runtime.memory.flush_once()
        self.assertEqual(self.runtime.memory.status()['deliveries'], {'completed': 1})
        self.assertEqual(len([c for c in self.calls if c[1].endswith('/messages/batch')]), 1)
        recall = self.runtime.memory.recall(self.ident, '回答风格', self.runtime.settings())
        self.assertIn('中文', recall[0]['content'])
        self.assertEqual(recall[0]['role'], 'user')
        self.assertIn('不能作为操作授权', recall[0]['content'])
        call = self.calls[-1]
        self.assertEqual(call[2]['target_uri'], 'viking://~/memories')
        self.assertEqual(call[3]['X-Api-Key'], 'fixture-key')
        self.assertNotIn('X-Openviking-User', call[3])
        self.assertNotIn('fixture-key', json.dumps(self.runtime.snapshot()))

    def test_lost_batch_and_commit_responses_do_not_duplicate(self):
        self.enqueue(); self.fail_batch_response = True
        self.runtime.memory.flush_once()
        self.assertEqual(self.runtime.memory.status()['deliveries'], {'pending': 1})
        self.fail_commit_response = True; self.runtime.memory.flush_once()
        self.runtime.memory.flush_once()
        self.assertEqual(self.runtime.memory.status()['deliveries'], {'extracting': 1})
        self.assertEqual(len([c for c in self.calls if c[1].endswith('/messages/batch')]), 1)
        self.assertEqual(len([c for c in self.calls if c[1].endswith('/commit')]), 1)

    def test_failed_extraction_is_visible_without_certifying_success(self):
        self.enqueue(); self.remote_status = 'failed'; self.runtime.memory.flush_once()
        self.assertEqual(self.runtime.memory.status()['deliveries'], {'failed': 1})
        self.assertIn('failed', self.runtime.memory.status()['last_error'])
        self.assertFalse(any(e['kind'] == 'memory_saved' for e in self.runtime.store.events(self.ident)))

    def test_recall_outage_offline_and_local_network_rules(self):
        self.fail_search = True
        self.assertEqual(self.runtime.memory.recall(self.ident, 'test', self.runtime.settings()), [])
        self.assertIn('503', self.runtime.memory.status()['last_error'])
        self.runtime.offline = True; self.enqueue()
        before = len(self.calls)
        self.assertEqual(self.runtime.memory.recall(self.ident, 'test', self.runtime.settings()), [])
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.runtime.memory.status()['deliveries'], {})
        self.runtime.offline = False
        self.runtime.memory.client.url = 'https://memory.example.com'
        self.assertFalse(self.runtime.memory.allowed({'mode': 'local'}))

    def test_deleting_session_clears_pending_delivery(self):
        self.enqueue(); self.runtime.session_action(self.sid, 'delete')
        self.assertEqual(self.runtime.memory.status()['deliveries'], {})
        self.runtime.memory.flush_once(); self.assertEqual(self.calls, [])

    def test_config_validation_and_trusted_headers(self):
        with self.assertRaises(ValueError): OpenVikingClient({'url': 'file:///tmp'}, {})
        with self.assertRaises(ValueError): OpenVikingClient({'timeout': 100}, {})
        client = OpenVikingClient({'auth_mode': 'trusted'}, {'OPENVIKING_API_KEY': 'secret'})
        self.assertEqual(client.headers['X-OpenViking-User'], 'marvisxy')
        with patch.object(client.opener, 'open', side_effect=OSError('secret')):
            with self.assertRaises(MemoryError) as caught: client.request('GET', '/health')
        self.assertNotIn('secret', str(caught.exception))

    def test_runtime_recalls_before_chat_and_enqueues_only_success(self):
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), patch.object(LLMClient, 'chat', return_value=ChatResult.success('好的，用中文。')) as chat:
            task = self.runtime.submit('我的回答风格是什么？', self.sid)
            self.runtime.pool.submit(lambda: None).result(timeout=5)
        self.assertEqual(self.runtime.store.one('SELECT status FROM tasks WHERE id=?', (task['id'],))['status'], 'completed')
        provider_messages = chat.call_args.args[0]
        self.assertIn('长期记忆', provider_messages[1]['content'])
        self.assertEqual(provider_messages[-1]['content'], '我的回答风格是什么？')
        self.assertEqual(self.runtime.memory.status()['deliveries'], {'pending': 1})
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), patch.object(LLMClient, 'chat', return_value=ChatResult.failure('模型失败')):
            self.runtime.submit('失败的自然语言请求', self.sid)
            self.runtime.pool.submit(lambda: None).result(timeout=5)
        self.assertEqual(self.runtime.memory.status()['deliveries'], {'pending': 1})
        self.runtime.submit('/tool agent_inspect {}', self.sid)
        self.runtime.pool.submit(lambda: None).result(timeout=5)
        self.assertEqual(self.runtime.memory.status()['deliveries'], {'pending': 1})

    def test_setup_preserves_credentials_and_converts_host_loopback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'config.yaml').write_text('llm: {}\n')
            (root / '.env').write_text('OPENVIKING_API_KEY=existing-secret\nOPENAI_API_KEY=chat-secret\n')
            with patch('scripts.setup_openviking.LLMClient', return_value=SimpleNamespace(base_url='http://localhost:11434/v1', model='local-model', api_key='chat-secret')), patch('builtins.print'):
                configure(root)
                configure(root)
            env = load_env_file(root / '.env')
            self.assertEqual(env['OPENVIKING_API_KEY'], 'existing-secret')
            self.assertEqual(env['OPENVIKING_VLM_API_KEY'], 'chat-secret')
            self.assertEqual(env['OPENVIKING_VLM_BASE_URL'], 'http://host.docker.internal:11434/v1')
            self.assertEqual((root / '.env').stat().st_mode & 0o777, 0o600)


if __name__ == '__main__': unittest.main()
