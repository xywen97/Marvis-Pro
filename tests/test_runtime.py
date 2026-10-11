import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from marvis.runtime import Runtime
from marvis.capabilities import Capabilities
from llm_client import ChatResult, LLMClient


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work = self.root / 'work'; self.work.mkdir()
        self.runtime = Runtime(self.root / 'data', offline=True)
        self.runtime.update_settings({'roots': [str(self.work)]})

    def tearDown(self):
        self.runtime.close(); self.tmp.cleanup()

    def wait(self, task, statuses=('completed','failed','cancelled'), timeout=5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            row = self.runtime.store.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
            if row['status'] in statuses: return row
            time.sleep(.01)
        self.fail('Task did not reach expected state: ' + str(row))

    def submit(self, tool, arguments):
        return self.runtime.submit('test ' + tool, action={'tool': tool, 'arguments': arguments})

    def test_search_index_and_persistence(self):
        (self.work / '合同.md').write_text('客户：上海公司', encoding='utf-8')
        (self.work / '.env').write_text('SECRET=never-index-me')
        t = self.submit('knowledge_index', {'path': str(self.work)})
        self.assertEqual(self.wait(t)['status'], 'completed')
        result = Capabilities(self.runtime.store).execute('knowledge_search', {'query': '上海'})
        self.assertEqual(len(result['files']), 1)
        self.assertEqual(Capabilities(self.runtime.store).execute('knowledge_search', {'query': 'never-index-me'})['files'], [])
        sid = t['session_id']
        self.runtime.close()
        self.runtime = Runtime(self.root / 'data', offline=True)
        self.assertEqual(len(self.runtime.messages(sid)), 2)
        self.assertEqual(self.runtime.snapshot()['knowledge_count'], 1)

    def test_paths_symlinks_and_internal_data_denied(self):
        outside = self.root / 'secret.txt'; outside.write_text('private')
        link = self.work / 'link.txt'; link.symlink_to(outside)
        caps = Capabilities(self.runtime.store)
        with self.assertRaises(PermissionError): caps.read(str(link))
        with self.assertRaises(PermissionError): caps.read(str(self.work / '../secret.txt'))
        (self.work / '.env').write_text('SECRET')
        with self.assertRaises(PermissionError): caps.read(str(self.work / '.env'))
        self.assertEqual(caps.execute('file_list', {'path': str(self.work)})['files'], [])

    def test_write_requires_confirmation_and_no_overwrite(self):
        path = self.work / 'note.md'
        task = self.submit('file_write', {'path': str(path), 'text': '实际内容'})
        self.wait(task, ('waiting_approval',))
        self.assertFalse(path.exists())
        self.runtime.approve(task['id'], True)
        self.assertEqual(self.wait(task)['status'], 'completed')
        self.assertEqual(path.read_text(), '实际内容')
        task = self.submit('file_write', {'path': str(path), 'text': 'overwrite'})
        self.assertEqual(self.wait(task)['status'], 'failed')
        self.assertEqual(path.read_text(), '实际内容')

    def test_reject_and_cancel_never_mutate(self):
        for reject in (True, False):
            p = self.work / ('reject.txt' if reject else 'cancel.txt')
            task = self.submit('file_write', {'path': str(p), 'text': 'danger'})
            self.wait(task, ('waiting_approval',))
            if reject: self.runtime.approve(task['id'], False)
            else: self.runtime.cancel(task['id'])
            self.wait(task)
            self.assertFalse(p.exists())

    def test_organize_and_stale_snapshot(self):
        p = self.work / 'first.txt'; p.write_text('hello')
        task = self.submit('file_organize', {'path': str(self.work)})
        self.wait(task, ('waiting_approval',)); p.write_text('changed after preview')
        self.runtime.approve(task['id'], True)
        self.assertEqual(self.wait(task)['status'], 'failed')
        self.assertTrue(p.exists())
        task = self.submit('file_organize', {'path': str(self.work)})
        self.wait(task, ('waiting_approval',)); self.runtime.approve(task['id'], True)
        self.assertEqual(self.wait(task)['status'], 'completed')
        self.assertFalse(p.exists()); self.assertEqual((self.work / '文档/first.txt').read_text(), 'changed after preview')

    def test_convert_and_duplicates(self):
        (self.work / 'data.csv').write_text('name,value\n测试,3\n', encoding='utf-8')
        task = self.submit('document_convert', {'path': str(self.work / 'data.csv'), 'format': 'json'})
        self.wait(task, ('waiting_approval',)); self.runtime.approve(task['id'], True)
        self.assertEqual(self.wait(task)['status'], 'completed')
        self.assertEqual(json.loads((self.work / 'data.json').read_text()), [{'name':'测试','value':'3'}])
        (self.work / 'a.txt').write_text('identical'); (self.work / 'b.txt').write_text('identical')
        result = Capabilities(self.runtime.store).execute('file_duplicates', {'path': str(self.work)})
        self.assertEqual(len(result['duplicates']), 1)

    def test_local_mode_blocks_network_tools(self):
        caps = Capabilities(self.runtime.store, local=True)
        with self.assertRaises(PermissionError): caps.execute('browser_read', {'url':'https://example.com'})
        with self.assertRaises(PermissionError): caps.execute('web_search', {'query':'x'})
        self.runtime.update_settings({'mode':'local'})
        client = self.runtime.model(self.runtime.settings())
        self.assertEqual(client.base_url, 'http://127.0.0.1:11434/v1')
        self.assertEqual(client.api_key, 'local')
        self.runtime.store.set_setting('local_model', {'base_url':'https://example.com', 'model':'x'})
        with self.assertRaises(PermissionError): self.runtime.model(self.runtime.settings())

    def test_model_multitool_loop_and_history(self):
        self.runtime.offline = False
        replies = [ChatResult.success('', tool_calls=[{'id':'call_1','type':'function','function':{'name':'system_info','arguments':'{"section":"disk"}'}}]), ChatResult.success('磁盘已检查')]
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), patch.object(LLMClient, 'chat', side_effect=replies) as chat:
            task = self.runtime.submit('请评估我的电脑健康状况')
            self.assertEqual(self.wait(task)['result'], '磁盘已检查')
            sent = chat.call_args_list[-1].args[0]
            self.assertTrue(any(m['role']=='tool' for m in sent))
            self.assertTrue(any(m['role']=='assistant' and m.get('tool_calls') for m in sent))

    def test_model_failure_is_visible(self):
        self.runtime.offline = False
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), patch.object(LLMClient, 'chat', return_value=ChatResult.failure('接口失败')):
            task = self.runtime.submit('帮我写一篇介绍')
            result = self.wait(task)
            self.assertEqual(result['status'], 'failed'); self.assertIn('接口失败',result['result'])

    def test_workflow_scheduler_and_pause(self):
        w = self.runtime.add_workflow('巡检','检查磁盘空间',60)
        self.runtime.store.execute('UPDATE workflows SET next_run=? WHERE id=?', (time.time()-1,w['id']))
        deadline = time.time()+4
        while time.time()<deadline:
            row=self.runtime.store.one('SELECT * FROM workflows WHERE id=?',(w['id'],))
            if row['last_task']:break
            time.sleep(.05)
        self.assertTrue(row['last_task']); self.wait({'id':row['last_task']})
        self.runtime.workflow_action(w['id'],'toggle')
        self.assertEqual(self.runtime.store.one('SELECT enabled FROM workflows WHERE id=?',(w['id'],))['enabled'],0)
        self.runtime.workflow_action(w['id'],'delete')
        self.assertIsNone(self.runtime.store.one('SELECT * FROM workflows WHERE id=?',(w['id'],)))

    def test_settings_cannot_change_during_task(self):
        t = self.submit('file_write', {'path': str(self.work / 'test.txt'), 'text': 'x'})
        self.wait(t, ('waiting_approval',))
        with self.assertRaises(ValueError): self.runtime.update_settings({'mode':'local'})
        self.runtime.cancel(t['id']); self.wait(t)

    def test_queued_turns_do_not_leak_future_messages(self):
        self.runtime.offline = False
        calls = []
        def reply(messages, **kwargs):
            calls.append([dict(m) for m in messages])
            return ChatResult.success('回答' + str(len(calls)))
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), patch.object(LLMClient, 'chat', side_effect=reply):
            sid = self.runtime.new_session()
            a = self.runtime.submit('第一个问题', sid)
            b = self.runtime.submit('第二个问题', sid)
            self.wait(a); self.wait(b)
        self.assertEqual([m['content'] for m in calls[0] if m['role'] == 'user'], ['第一个问题'])
        self.assertEqual([(m['role'], m['content']) for m in calls[1][1:]], [('user','第一个问题'),('assistant','回答1'),('user','第二个问题')])
