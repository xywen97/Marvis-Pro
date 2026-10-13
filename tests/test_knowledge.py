import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock
from marvis.runtime import Runtime
from marvis.capabilities import Capabilities
from marvis.memory import MemoryError


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'; self.work.mkdir()
        self.runtime = Runtime(self.root / 'data', config={'llm': {'env_file': str(self.root / '.env')}}, offline=True)
        self.runtime.update_settings({'roots': [str(self.work)]})
        self.engine = self.runtime.knowledge_engine
        self.engine.close()  # Drive the durable queue deterministically.
        self.engine.stop.clear()
        self.client = Mock()
        self.runtime.memory.client = self.client
        self.engine.available = lambda: True
        self.engine.upload = Mock(return_value='uploaded-text')
        self.client.request.return_value = {'task_id': 'task-1'}

    def tearDown(self):
        self.runtime.close(); self.tmp.cleanup()

    def index(self, name='note.md', text='用户偏好和长期记忆由 OpenViking 保存。'):
        p = self.work / name; p.write_text(text)
        Capabilities(self.runtime.store).execute('knowledge_index', {'path': str(self.work)})
        return p

    def test_default_browse_pagination_type_and_literal_keyword(self):
        for i in range(27): self.index(f'note-{i:02d}.md', '预算 50% _draft 上海')
        first = self.runtime.knowledge()
        self.assertEqual(first['total'], 27); self.assertEqual(len(first['files']), 24)
        self.assertTrue(first['has_more']); self.assertEqual(len(self.runtime.knowledge(offset=24)['files']), 3)
        self.assertEqual(self.runtime.knowledge('  ')['total'], 27)
        self.assertEqual(self.runtime.knowledge('50% _draft')['total'], 27)
        self.assertEqual(self.runtime.knowledge(category='图片')['total'], 0)
        self.assertNotIn('text', first['files'][0])
        for args in ({'mode': 'invalid'}, {'offset': -1}, {'query': 'a'*4001}):
            with self.assertRaises(ValueError): self.runtime.knowledge(**args)

    def test_async_ingestion_ready_only_after_completion_and_original_source(self):
        p = self.index()
        def upload(text):
            status = self.engine.status()
            self.assertEqual(status['failed'], 0)
            self.assertEqual(status['processing'], 1)
            return 'uploaded-text'
        self.engine.upload.side_effect = upload
        self.engine.sync_once()
        self.assertEqual(self.engine.status()['processing'], 1)
        self.assertEqual(self.engine.status()['ready'], 0)
        self.engine.upload.assert_called_once()
        body = self.client.request.call_args.args[2]
        self.assertEqual(body['temp_file_id'], 'uploaded-text')
        self.assertFalse(body['wait']); self.assertTrue(body['to'].startswith(self.engine.prefix))
        self.client.request.return_value = {'status': 'completed'}
        self.engine.sync_once()
        self.assertEqual(self.engine.status()['ready'], 1)
        r = self.runtime.store.one('SELECT * FROM knowledge_resources')
        self.client.request.return_value = {'resources': [{'uri': r['uri']+'/section.md', 'abstract': '相关含义', 'score': .8}, {'uri': r['uri'], 'abstract': '重复'}, {'uri': 'viking://resources/other', 'abstract': '别人的资料'}]}
        found = self.runtime.knowledge('我是谁', 'semantic')
        self.assertEqual(len(found['files']), 1)
        self.assertEqual(found['files'][0]['path'], str(p.resolve())); self.assertEqual(found['files'][0]['preview'], '相关含义')
        self.assertEqual(found['files'][0]['score'], .8)
        caps = Capabilities(self.runtime.store); caps.knowledge_engine = self.engine
        self.assertEqual(len(caps.execute('knowledge_search', {'query': '我是谁', 'mode': 'semantic'})['files']), 1)
        self.assertEqual(self.runtime.knowledge_preview(str(p))['text'], p.read_text())
        self.engine.sync_once = Mock()

    def test_stale_deleted_and_revoked_files_excluded_and_cleaned(self):
        p = self.index(); self.engine.sync_once()
        self.client.request.return_value = {'status': 'completed'}; self.engine.sync_once()
        p.write_text('更新后的知识，比原来长。')
        self.assertEqual(self.runtime.knowledge()['files'][0]['semantic_status'], 'stale')
        self.client.request.return_value = {'resources': []}
        self.assertEqual(self.runtime.knowledge('记忆', 'semantic')['files'], [])
        self.engine.sync_once()
        self.assertEqual(self.runtime.store.rows('SELECT * FROM knowledge_resources'), [])
        self.assertTrue(any(c.args[:2] == ('DELETE', '/api/v1/fs') for c in self.client.request.call_args_list))
        p.unlink(); self.assertEqual(self.runtime.knowledge()['total'], 0)
        p = self.index()
        outside = self.root/'other'; outside.mkdir()
        self.runtime.update_settings({'roots': [str(outside)]})
        self.assertEqual(self.runtime.knowledge()['files'], [])
        self.assertEqual(self.runtime.snapshot()['knowledge_count'], 0)
        with self.assertRaises(PermissionError): self.runtime.knowledge_preview(str(p))

    def test_changed_file_waits_for_inflight_job_before_retirement(self):
        p = self.index(); self.engine.sync_once(); p.unlink()
        self.client.request.return_value = {'status': 'running'}
        self.engine.sync_once(); self.assertEqual(len(self.runtime.store.rows('SELECT * FROM knowledge_resources')), 1)
        self.client.request.return_value = {'status': 'completed'}
        self.engine.sync_once(); self.assertEqual(self.runtime.store.rows('SELECT * FROM knowledge_resources'), [])

    def test_failure_retry_and_unavailable_are_explicit(self):
        self.index(); self.engine.upload.side_effect = MemoryError('failed')
        self.engine.sync_once(); self.assertEqual(self.engine.status()['failed'], 1)
        self.engine.upload.side_effect = None
        self.client.request.side_effect = lambda method, path, *a, **k: [] if path == '/api/v1/tasks' else {'task_id': 'task-1'}
        self.engine.retry(); self.engine.sync_once()
        self.assertEqual(self.engine.status()['processing'], 1)
        self.client.request.side_effect = None
        self.client.request.return_value = {'status': 'failed'}; self.engine.sync_once()
        self.assertEqual(self.engine.status()['failed'], 1)
        self.engine.available = lambda: False
        result = self.runtime.knowledge('自然语言问题', 'semantic')
        self.assertEqual(result['status'], 'unavailable'); self.assertNotEqual(result['notice'], '')
        self.assertEqual(self.runtime.knowledge()['total'], 1)

    def test_lost_submission_response_recovers_without_duplicate_upload(self):
        self.index()
        self.client.request.side_effect = MemoryError('response lost')
        self.engine.sync_once(); self.assertEqual(self.engine.status()['failed'], 1)
        self.client.request.side_effect = None
        self.client.request.return_value = [{'task_id': 'accepted-task', 'status': 'completed'}]
        self.engine.retry(); self.engine.sync_once()
        self.assertEqual(self.engine.status()['ready'], 1)
        self.assertEqual(self.engine.upload.call_count, 1)

    def test_completed_job_survives_restart(self):
        self.index(); self.engine.sync_once()
        self.client.request.return_value = {'status': 'completed'}; self.engine.sync_once()
        self.runtime.close()
        self.runtime = Runtime(self.root/'data', offline=True)
        self.engine = self.runtime.knowledge_engine
        self.assertEqual(self.engine.status()['ready'], 1)


if __name__ == '__main__': unittest.main()
