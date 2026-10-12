"""User-triggered assessment with the real DeepEval SDK and deterministic judges."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from llm_client import LLMClient, ChatResult
from marvis.runtime import Runtime
from marvis.coordinator import Coordinator
from marvis.capabilities import Capabilities
from marvis.agent_protocol import RESULT_SCHEMA, validate
from marvis.store import uid


@unittest.skipUnless(importlib.util.find_spec('deepeval'), 'Install requirements-eval.txt')
class UserEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.runtime = Runtime(self.root / 'data')
        self.runtime.update_settings({'roots': [str(self.root)]})
        self.sid = self.runtime.new_session()
        self.request = {'candidate': 'answer = 42', 'requirements': '结果必须为 42', 'subject_type': '代码'}
        self.main_inputs = []

    def tearDown(self):
        self.runtime.close(); self.temp.cleanup()

    def run_task(self, judge=None, request=True, prompt='用户主动评测', main_response='根据用户主动评测结果，要求已满足。'):
        def respond(messages, **kwargs):
            if '以下是用户主动唤醒评测助手' in messages[0]['content']:
                self.main_inputs.append((messages, kwargs))
                if callable(main_response): return main_response(messages, **kwargs)
                return ChatResult.success(main_response) if main_response else ChatResult.failure('主控不可用')
            if judge: return judge(messages, **kwargs)
            return ChatResult.success('{"score":10,"reason":"符合用户指定要求"}' if not kwargs['tools'] else '普通任务结果')
        action = {'tool': 'delivery_evaluate', 'arguments': self.request} if request else None
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), \
                patch.object(LLMClient, 'chat', side_effect=respond):
            task = self.runtime.submit(prompt, self.sid, action)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                row = self.runtime.store.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
                if row['status'] in ('completed', 'failed', 'cancelled'):
                    return row, self.runtime.store.events(task['id'])
                time.sleep(.02)
            self.runtime.cancel(task['id']); self.fail('Task timed out')

    def source_task(self, path):
        ident = uid(); now = time.time()
        self.runtime.store.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)',
            (ident, self.sid, '旧任务', 'completed', '已创建', now, now))
        self.runtime.store.add_message(self.sid, 'user', '旧任务', ident)
        self.runtime.store.event(ident, 'tool_result', {'agent': 'coding', 'tool': 'file_write', 'result': {'created': str(path)}})
        self.runtime.store.event(ident, 'agent_context', {'agent': 'coding', 'messages': [{'role': 'assistant', 'content': 'PRIVATE_WORKER'}]})
        self.runtime.store.add_message(self.sid, 'assistant', '已创建', ident)
        return ident

    def result(self, events):
        return next(e['content']['result'] for e in events if e['kind'] == 'user_evaluation_result')

    def test_ordinary_answer_never_invokes_evaluation_even_with_missing_dependency(self):
        self.runtime.config['evaluation'] = {'python': '/nonexistent/python'}
        with patch('marvis.evaluation.subprocess.Popen') as spawn:
            row, events = self.run_task(request=False, prompt='检查电脑状态')
        self.assertEqual(row['status'], 'completed'); self.assertEqual(row['result'], '普通任务结果')
        spawn.assert_not_called()
        self.assertFalse(any(e['kind'].startswith('user_evaluation') for e in events))

    def test_direct_tool_result_does_not_invoke_evaluation(self):
        p = self.root / 'source.txt'; p.write_text('真实内容')
        task = self.runtime.submit('读取文件', self.sid, {'tool': 'file_read', 'arguments': {'path': str(p)}})
        deadline = time.monotonic() + 3
        while task['id'] in self.runtime.cancels and time.monotonic() < deadline: time.sleep(.01)
        row = self.runtime.store.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
        self.assertEqual(row['status'], 'completed', row['result'])
        self.assertFalse(any(e['content'].get('agent') == 'evaluation' for e in self.runtime.store.events(task['id'])))

    def test_main_cannot_automatically_dispatch_evaluation(self):
        ident = uid(); now = time.time()
        self.runtime.store.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)', (ident, self.sid, '普通任务', 'completed', '', now, now))
        coordinator = Coordinator(self.runtime, ident, self.sid, Capabilities(self.runtime.store), self.runtime.settings())
        with self.assertRaisesRegex(PermissionError, '用户显式唤醒'):
            coordinator.dispatch({'agent': 'evaluation', 'objective': '自行验收', 'context': '结果'})

    def test_wakeup_requests_information_without_model_calls(self):
        def judge(*args, **kwargs): self.fail('Intake must not call a model')
        for prompt in ('唤醒评测助手', '/evaluate', '/评测'):
            row, events = self.run_task(judge, request=False, prompt=prompt)
            self.assertEqual(row['status'], 'completed')
            self.assertIn('评测对象', row['result']); self.assertIn('评测要求', row['result'])
            self.assertTrue(any(e['kind'] == 'evaluation_intake' for e in events))
            self.assertFalse(any(e['kind'] == 'agent_start' for e in events))

    def test_missing_and_blank_requirements_are_rejected_before_submission(self):
        for args in ({'candidate': 'code'}, {'candidate': 'code', 'requirements': ' '}):
            with self.assertRaises(ValueError):
                self.runtime.submit('评测', self.sid, {'tool': 'delivery_evaluate', 'arguments': args})
        self.assertEqual(self.runtime.snapshot()['tasks'], [])

    def test_sdk_result_and_original_request_return_to_main(self):
        self.request.update(paths=[], evidence='')
        with patch('marvis.evaluation.subprocess.Popen', wraps=subprocess.Popen) as spawn:
            row, events = self.run_task()
        self.assertEqual(spawn.call_args.args[0][0], sys.executable)
        self.assertEqual(row['status'], 'completed', row['result'])
        result = self.result(events)
        self.assertTrue(result['passed']); self.assertTrue(result['user_initiated'])
        self.assertEqual(result['framework'], 'deepeval'); self.assertEqual(result['judge_calls'], 3)
        self.assertEqual([m['name'] for m in result['metrics']], ['要求符合度', '证据一致性', '结果适用性'])
        envelope = next(e['content']['result'] for e in events if e['kind'] == 'agent_result')
        validate(envelope, RESULT_SCHEMA)
        handoff = next(e['content'] for e in events if e['kind'] == 'evaluation_handoff')
        self.assertEqual(handoff['request'], self.request); self.assertTrue(handoff['user_initiated'])
        messages, kwargs = self.main_inputs[0]
        self.assertEqual(kwargs['tool_choice'], 'none'); self.assertEqual(kwargs['tools'], [])
        payload = json.loads(messages[-1]['content'])
        self.assertEqual(payload['request']['requirements'], self.request['requirements'])
        self.assertEqual(payload['result'], result)
        self.assertIn('用户主动评测', row['result']); self.assertIn(self.request['requirements'], row['result'])
        self.assertFalse(any(e['kind'] == 'delivery_accepted' for e in events))

    def test_successful_file_write_still_completes_without_judge(self):
        self.runtime.config['evaluation'] = {'python': '/nonexistent/python'}
        p = self.root / 'new.txt'
        task = self.runtime.submit('创建文件', self.sid, {'tool': 'file_write', 'arguments': {'path': str(p), 'text': '实际内容'}})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            row = self.runtime.store.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
            if row['status'] == 'waiting_approval': break
            time.sleep(.01)
        self.assertEqual(row['status'], 'waiting_approval')
        self.runtime.approve(task['id'], True)
        while task['id'] in self.runtime.cancels and time.monotonic() < deadline: time.sleep(.01)
        row = self.runtime.store.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
        self.assertEqual(row['status'], 'completed', row['result']); self.assertEqual(p.read_text(), '实际内容')
        self.assertFalse(any(e['kind'] == 'user_evaluation_request' for e in self.runtime.store.events(task['id'])))

    def test_low_scores_complete_assessment_without_automatic_repair(self):
        row, events = self.run_task(lambda m, **kw: ChatResult.success('{"score":2,"reason":"缺少所需结果"}'), main_response=None)
        self.assertEqual(row['status'], 'completed', row['result'])
        self.assertFalse(self.result(events)['passed']); self.assertIn('未满足全部', row['result'])
        self.assertEqual(sum(e['kind'] == 'agent_start' for e in events), 1)
        self.assertTrue(all(m['threshold'] == .8 for m in self.result(events)['metrics']))

    def test_missing_score_corrects_each_metric_and_audits_nine_calls(self):
        count = 0
        def judge(messages, **kwargs):
            nonlocal count
            count += 1
            if count % 3: return ChatResult.success('{"reason":"遗漏分数"}')
            self.assertIn('validation_error', messages[-1]['content']); self.assertIn('score', messages[-1]['content'])
            return ChatResult.success('{"score":10,"reason":"满足要求"}')
        row, events = self.run_task(judge)
        self.assertEqual(row['status'], 'completed', row['result']); self.assertEqual(count, 9)
        self.assertEqual(self.result(events)['judge_calls'], 9)

    def test_invalid_scores_fail_after_bounded_retries_and_reach_main(self):
        for content in ('not JSON', '{"reason":"没有分数"}'):
            count = 0
            def judge(*args, **kwargs):
                nonlocal count
                count += 1; return ChatResult.success(content)
            row, events = self.run_task(judge, main_response=None)
            self.assertEqual(row['status'], 'failed'); self.assertEqual(count, 3)
            self.assertIn('评测未能完成', row['result']); self.assertIn('重试两次后仍无效', row['result'])
            self.assertTrue(any(e['kind'] == 'evaluation_handoff' for e in events))
            self.assertFalse(any(e['kind'] == 'user_evaluation_result' for e in events))

    def test_missing_dependency_does_not_block_future_ordinary_tasks(self):
        self.runtime.config['evaluation'] = {'python': '/nonexistent/python'}
        row, events = self.run_task(main_response=None)
        self.assertEqual(row['status'], 'failed'); self.assertIn('评测未能完成', row['result'])
        self.assertTrue(any(e['kind'] == 'user_evaluation_summary' for e in events))
        ordinary, _ = self.run_task(request=False)
        self.assertEqual(ordinary['status'], 'completed')

    def test_disabled_manual_evaluation_does_not_create_scores(self):
        self.runtime.config['evaluation'] = {'enabled': False}
        row, events = self.run_task(main_response=None)
        self.assertEqual(row['status'], 'failed'); self.assertIn('已禁用', row['result'])
        self.assertFalse(any(e['kind'] == 'user_evaluation_result' for e in events))

    def test_files_and_history_are_read_without_private_worker_context(self):
        p = self.root / 'artifact.py'; p.write_text('answer = 42\n')
        self.request.update(source_task_id=self.source_task(p), paths=[str(p)], evidence='用户提供的测试：answer == 42')
        def judge(messages, **kwargs):
            prompt = messages[-1]['content']
            self.assertIn('answer = 42', prompt); self.assertIn('未经执行器独立验证', prompt)
            self.assertIn('用户提供的测试', prompt); self.assertNotIn('PRIVATE_WORKER', prompt)
            return ChatResult.success('{"score":10,"reason":"满足要求"}')
        row, _ = self.run_task(judge)
        self.assertEqual(row['status'], 'completed', row['result'])

    def test_cross_session_source_task_is_rejected(self):
        p = self.root / 'artifact.py'; p.write_text('x=1')
        self.request['source_task_id'] = self.source_task(p)
        self.sid = self.runtime.new_session(self.sid)
        row, events = self.run_task(main_response=None)
        self.assertEqual(row['status'], 'failed'); self.assertIn('当前对话', row['result'])
        self.assertFalse(any(e['kind'] == 'user_evaluation_result' for e in events))

    def test_changed_file_invalidates_result(self):
        p = self.root / 'changing.py'; p.write_text('x=1'); self.request['paths'] = [str(p)]
        def judge(*args, **kwargs):
            p.write_text('x=2'); return ChatResult.success('{"score":10,"reason":"满足要求"}')
        row, events = self.run_task(judge, main_response=None)
        self.assertEqual(row['status'], 'failed'); self.assertIn('产物发生变化', row['result'])
        self.assertFalse(any(e['kind'] == 'user_evaluation_result' for e in events))

    def test_judge_cannot_call_other_agents(self):
        from agent_fixture import dispatch_reply
        self.runtime.update_settings({'mode': 'local'})
        row, _ = self.run_task(lambda *a, **kw: dispatch_reply('coding', '越权'), main_response=None)
        self.assertEqual(row['status'], 'failed'); self.assertIn('非法工具调用', row['result'])

    def test_cancellation_terminates_subprocess(self):
        processes = []; original = subprocess.Popen
        def spawn(*args, **kwargs):
            process = original(*args, **kwargs); processes.append(process); return process
        def judge(*args, **kwargs):
            task = self.runtime.store.one("SELECT id FROM tasks WHERE status='running'")
            self.runtime.cancel(task['id']); return ChatResult.success('{"score":10,"reason":"满足要求"}')
        with patch('marvis.evaluation.subprocess.Popen', side_effect=spawn): row, _ = self.run_task(judge)
        self.assertEqual(row['status'], 'cancelled'); self.assertTrue(processes)
        self.assertTrue(all(p.poll() is not None for p in processes))

    def test_timeout_preserves_request_and_returns_unavailable_to_main(self):
        interpreter = self.root / 'slow-python'
        interpreter.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(30)\n'); interpreter.chmod(0o755)
        self.runtime.config['evaluation'] = {'python': str(interpreter), 'timeout': 10}
        processes = []; original = subprocess.Popen
        def spawn(*args, **kwargs):
            process = original(*args, **kwargs); processes.append(process); return process
        with patch('marvis.evaluation.subprocess.Popen', side_effect=spawn): row, events = self.run_task(main_response=None)
        self.assertEqual(row['status'], 'failed'); self.assertIn('超时', row['result'])
        self.assertIn(self.request['requirements'], row['result'])
        self.assertTrue(any(e['kind'] == 'evaluation_handoff' for e in events))
        self.assertTrue(all(p.poll() is not None for p in processes))
