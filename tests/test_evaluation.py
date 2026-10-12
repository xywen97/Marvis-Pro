"""Actual DeepEval SDK with deterministic judge replies, not a mock scoring engine."""
import json
import importlib.util
import subprocess
import sys
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from llm_client import LLMClient, ChatResult
from marvis.runtime import Runtime
from marvis.agent_protocol import RESULT_SCHEMA, validate
from agent_fixture import dispatch_reply, report_reply, tool_reply


@unittest.skipUnless(importlib.util.find_spec('deepeval'), 'Install requirements-eval.txt in the project environment')
class DeepEvalIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.runtime = Runtime(self.root / 'data')
        self.runtime.update_settings({'roots': [str(self.root)]})

    def tearDown(self):
        self.runtime.close(); self.temp.cleanup()

    def run_task(self, respond, prompt='交付一份简明的结果说明', failure_response='本次交付未通过评测。请根据失败原因继续修正；已完成的检查保留在执行记录中。'):
        original = respond
        def respond(messages, **kwargs):
            if '这是失败后的状态说明' in messages[0]['content']:
                self.assertEqual(kwargs['tool_choice'], 'none')
                if callable(failure_response): return failure_response(messages, **kwargs)
                return ChatResult.success(failure_response) if failure_response else ChatResult.failure('汇总模型不可用')
            return original(messages, **kwargs)
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), \
                patch.object(LLMClient, 'chat', side_effect=respond):
            task = self.runtime.submit(prompt)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                row = self.runtime.store.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
                if row['status'] in ('completed', 'failed', 'cancelled'): return row, self.runtime.store.events(task['id'])
                time.sleep(.02)
            self.runtime.cancel(task['id']); self.fail('Evaluation timed out')

    def test_real_sdk_accepts_and_emits_same_protocol_without_streaming_draft(self):
        calls = []
        def respond(messages, **kwargs):
            calls.append((messages, kwargs))
            if not kwargs['tools']: return ChatResult.success(json.dumps({'score': 10, 'reason': '目标与证据一致，交付清楚。'}))
            return ChatResult.success('实际交付内容')
        with patch('marvis.evaluation.subprocess.Popen', wraps=subprocess.Popen) as spawn:
            row, events = self.run_task(respond)
        self.assertEqual(spawn.call_args.args[0][0], sys.executable)
        self.assertEqual(row['status'], 'completed', row['result'])
        review = next(e['content']['result'] for e in events if e['kind'] == 'agent_result')
        validate(review, RESULT_SCHEMA); self.assertEqual(review['agent'], 'evaluation')
        metrics = review['evidence'][0]['result']
        self.assertEqual(metrics['framework'], 'deepeval'); self.assertEqual(metrics['version'], '4.2.8')
        self.assertTrue(metrics['passed']); self.assertEqual(len(metrics['metrics']), 3)
        self.assertEqual(len(calls), 4)
        self.assertFalse(any(e['kind'] == 'assistant_stream' for e in events))
        self.assertTrue(any(e['kind'] == 'delivery_accepted' for e in events))
        self.assertEqual(row['result'], '实际交付内容')

    def test_delivery_gate_remains_available_after_thirteen_worker_dispatches(self):
        self.runtime.config['orchestration'] = {'max_dispatches': 1}
        main_count = 0
        def respond(messages, **kwargs):
            nonlocal main_count
            names = {t['function']['name'] for t in kwargs['tools']}
            if not names: return ChatResult.success('{"score":10,"reason":"探测回执与结论一致"}')
            if 'agent_dispatch' not in names: return report_reply('轻量探测完成')
            main_count += 1
            if main_count > 2: return ChatResult.success('十三次轻量探测已返回，未验证未探测的能力。')
            calls = []
            for i in range(7 if main_count == 1 else 6):
                call = dispatch_reply('computer', f'连通性探测 {main_count}-{i}').tool_calls[0]
                call['id'] = f'dispatch-{main_count}-{i}'; calls.append(call)
            return ChatResult.success('', tool_calls=calls)
        row, events = self.run_task(respond)
        self.assertEqual(row['status'], 'completed', row['result'])
        starts = [e['content']['agent'] for e in events if e['kind'] == 'agent_start']
        self.assertEqual(starts.count('computer'), 13)
        self.assertEqual(starts.count('evaluation'), 1)
        self.assertTrue(any(e['kind'] == 'delivery_accepted' for e in events))

    def test_failure_feedback_reaches_main_and_new_candidate_is_reevaluated(self):
        main_count = 0; judge_calls = 0
        def respond(messages, **kwargs):
            nonlocal main_count, judge_calls
            if not kwargs['tools']:
                judge_calls += 1
                return ChatResult.success(json.dumps({'score': 3 if judge_calls <= 3 else 10, 'reason': '缺少结果细节' if judge_calls <= 3 else '修正后满足要求'}))
            main_count += 1
            if main_count == 2: self.assertIn('缺少结果细节', messages[-1]['content'])
            return ChatResult.success('初稿' if main_count == 1 else '修正后的完整交付')
        row, events = self.run_task(respond)
        self.assertEqual(row['status'], 'completed', row['result']); self.assertEqual(row['result'], '修正后的完整交付')
        self.assertEqual(judge_calls, 6)
        reviews = [e['content'] for e in events if e['kind'] == 'delivery_evaluation']
        self.assertEqual([r['passed'] for r in reviews], [False, True])
        self.assertNotEqual(reviews[0]['candidate_sha256'], reviews[1]['candidate_sha256'])

    def test_invalid_judge_json_fails_closed(self):
        row, events = self.run_task(lambda m, **kw: ChatResult.success('not JSON' if not kw['tools'] else '未评测成果'))
        self.assertEqual(row['status'], 'failed'); self.assertIn('未放行', row['result'])
        self.assertFalse(any(e['kind'] == 'delivery_accepted' for e in events))

    def test_missing_score_is_corrected_with_schema_feedback(self):
        calls = 0
        def respond(messages, **kwargs):
            nonlocal calls
            if kwargs['tools']: return ChatResult.success('完整交付')
            calls += 1
            if calls % 3 != 0: return ChatResult.success('{"reason":"只返回理由"}')
            self.assertIn('validation_error', messages[-1]['content'])
            self.assertIn('score', messages[-1]['content'])
            self.assertIn('只返回理由', messages[-1]['content'])
            return ChatResult.success('{"score":10,"reason":"任务与证据一致"}')
        row, events = self.run_task(respond)
        self.assertEqual(row['status'], 'completed', row['result'])
        self.assertEqual(calls, 9)
        receipt = next(e['content'] for e in events if e['kind'] == 'delivery_evaluation')
        self.assertEqual(receipt['judge_calls'], 9)
        self.assertTrue(all(m['threshold'] == .8 for m in receipt['metrics']))
        self.assertTrue(any(e['kind'] == 'delivery_accepted' for e in events))

    def test_persistently_missing_score_exhausts_retries_without_release(self):
        calls = 0
        def respond(messages, **kwargs):
            nonlocal calls
            if kwargs['tools']: return ChatResult.success('候选结果')
            calls += 1
            return ChatResult.success('{"reason":"没有分数"}')
        row, events = self.run_task(respond)
        self.assertEqual(calls, 3)
        self.assertEqual(row['status'], 'failed')
        self.assertIn('重试两次后仍无效', row['result'])
        self.assertFalse(any(e['kind'] == 'delivery_accepted' for e in events))

    def test_corrected_low_score_still_blocks_delivery(self):
        self.runtime.config['evaluation'] = {'max_repairs': 0}
        calls = 0
        def respond(messages, **kwargs):
            nonlocal calls
            if kwargs['tools']: return ChatResult.success('未完成候选')
            calls += 1
            return ChatResult.success('{"reason":"缺少成果"}' if calls == 1 else '{"score":2,"reason":"缺少成果"}')
        row, events = self.run_task(respond)
        self.assertEqual(row['status'], 'failed')
        receipt = next(e['content'] for e in events if e['kind'] == 'delivery_evaluation')
        self.assertFalse(receipt['passed'])
        self.assertTrue(all(m['threshold'] == .8 for m in receipt['metrics']))
        self.assertFalse(any(e['kind'] == 'delivery_accepted' for e in events))

    def test_failure_summary_receives_key_evidence_despite_large_worker_output(self):
        self.runtime.config['evaluation'] = {'python': '/nonexistent/python'}
        main_calls = 0; worker_calls = 0; summaries = []
        def respond(messages, **kwargs):
            nonlocal main_calls, worker_calls
            names = {t['function']['name'] for t in kwargs['tools']}
            if 'agent_dispatch' in names:
                main_calls += 1
                return dispatch_reply('computer', '检查状态') if main_calls == 1 else ChatResult.success('候选')
            worker_calls += 1
            return tool_reply('system_info', {}) if worker_calls == 1 else report_reply('状态检查完成；系统版本尚未验证')
        def summarize(messages, **kwargs):
            evidence = json.loads(messages[-1]['content']); summaries.append(evidence)
            self.assertEqual(evidence['reports']['computer']['status'], 'completed')
            self.assertIn('尚未验证', evidence['reports']['computer']['summary'])
            record = evidence['tool_records'][0]
            self.assertEqual(record['tool'], 'system_info')
            self.assertIn({'field': 'path', 'value': '/tmp/verified.txt'}, record['facts'])
            self.assertLessEqual(len(messages[-1]['content']), 18000)
            return ChatResult.success('电脑检查完成，版本未验证；评测环境不可用。')
        with patch('marvis.capabilities.Capabilities.system', return_value={'log': 'x' * 100000, 'path': '/tmp/verified.txt'}):
            row, events = self.run_task(respond, failure_response=summarize)
        self.assertEqual(row['status'], 'failed', row['result'])
        self.assertEqual(len(summaries), 1)
        self.assertIn('电脑检查完成，版本未验证；评测环境不可用。', row['result'])
        self.assertFalse(any(e['kind'] == 'delivery_accepted' for e in events))

    def test_retry_cap_does_not_deliver_failed_candidate(self):
        self.runtime.config['evaluation'] = {'max_repairs': 1}
        count = 0
        def respond(messages, **kwargs):
            nonlocal count
            if not kwargs['tools']: return ChatResult.success('{"score":2,"reason":"核心成果缺失"}')
            count += 1; return ChatResult.success('未完成成果' + str(count))
        row, events = self.run_task(respond)
        self.assertEqual(row['status'], 'failed'); self.assertEqual(count, 2)
        self.assertIn('核心成果缺失', row['result'])
        self.assertIn('主控汇总', row['result'])
        self.assertIn('本次交付未通过评测', row['result'])
        self.assertTrue(any(e['kind'] == 'failure_summary' for e in events))
        self.assertEqual(len([e for e in events if e['kind'] == 'delivery_candidate']), 2)
        self.assertFalse(any(e['kind'] == 'delivery_accepted' for e in events))

    def test_missing_dependency_fails_closed_but_legacy_model_cap_does_not_block_gate(self):
        self.runtime.config['evaluation'] = {'python': '/nonexistent/python'}
        row, events = self.run_task(lambda m, **kw: ChatResult.success('候选结果'))
        self.assertEqual(row['status'], 'failed'); self.assertFalse(any(e['kind'] == 'delivery_accepted' for e in events))
        self.runtime.config['evaluation'] = {}; self.runtime.config['orchestration'] = {'max_model_calls': 1}
        row, events = self.run_task(lambda m, **kw: ChatResult.success('{"score":10,"reason":"满足要求"}' if not kw['tools'] else '候选结果'))
        self.assertEqual(row['status'], 'completed', row['result'])
        self.assertTrue(any(e['kind'] == 'delivery_accepted' for e in events))

    def test_summary_model_failure_falls_back_to_completed_worker_reports(self):
        self.runtime.config['evaluation'] = {'python': '/nonexistent/python'}
        main_calls = 0
        def respond(messages, **kwargs):
            nonlocal main_calls
            names = {t['function']['name'] for t in kwargs['tools']}
            if 'agent_dispatch' in names:
                main_calls += 1
                return dispatch_reply('computer', '轻量状态检查') if main_calls == 1 else ChatResult.success('候选交付')
            return report_reply('电脑检查已返回，但系统版本尚未验证')
        row, events = self.run_task(respond, failure_response=None)
        self.assertEqual(row['status'], 'failed')
        self.assertIn('主控汇总', row['result'])
        self.assertIn('电脑管家', row['result'])
        self.assertIn('系统版本尚未验证', row['result'])
        self.assertFalse(any(e['kind'] == 'delivery_accepted' for e in events))
        self.assertTrue(any(e['kind'] == 'failure_summary' for e in events))

    def test_eval_context_uses_actual_artifact_and_no_other_agents_private_context(self):
        p = self.root / 'artifact.py'; p.write_text('answer = 42\n')
        main_count = 0; worker_count = 0; judge_prompts = []
        def respond(messages, **kwargs):
            nonlocal main_count, worker_count
            names = {t['function']['name'] for t in kwargs['tools']}
            if not names:
                judge_prompts.append(messages)
                self.assertIn('answer = 42', messages[-1]['content'])
                self.assertNotIn('PRIVATE_WORKER_REASONING', json.dumps(messages))
                return ChatResult.success('{"score":10,"reason":"产物与证据一致"}')
            if 'agent_dispatch' in names:
                main_count += 1
                return dispatch_reply('coding', '检查文件', str(p)) if main_count == 1 else ChatResult.success('代码语法已检查：' + str(p))
            worker_count += 1
            if worker_count == 1: return tool_reply('code_check', {'path': str(p)}, 'PRIVATE_WORKER_REASONING')
            return report_reply('文件语法通过')
        row, events = self.run_task(respond, '检查代码并交付检查结果')
        self.assertEqual(row['status'], 'completed', row['result']); self.assertEqual(len(judge_prompts), 3)

    def test_artifact_changed_during_evaluation_invalidates_pass(self):
        p = self.root / 'changing.py'; p.write_text('original = 1\n')
        main_count = 0; worker_count = 0
        def respond(messages, **kwargs):
            nonlocal main_count, worker_count
            names = {t['function']['name'] for t in kwargs['tools']}
            if not names:
                p.write_text('changed = 2\n')
                return ChatResult.success('{"score":10,"reason":"通过"}')
            if 'agent_dispatch' in names:
                main_count += 1
                return dispatch_reply('coding', '检查代码', str(p)) if main_count == 1 else ChatResult.success('交付检查结果')
            worker_count += 1
            return tool_reply('code_check', {'path': str(p)}) if worker_count == 1 else report_reply()
        row, events = self.run_task(respond)
        self.assertEqual(row['status'], 'failed'); self.assertIn('产物发生变化', row['result'])
        self.assertFalse(any(e['kind'] == 'delivery_accepted' for e in events))

    def test_local_judge_remains_loopback_and_cannot_call_tools(self):
        self.runtime.update_settings({'mode': 'local'})
        def respond(messages, **kwargs):
            if not kwargs['tools']: return dispatch_reply('coding', '越权调用')
            return ChatResult.success('本地候选结果')
        row, events = self.run_task(respond)
        self.assertEqual(row['status'], 'failed'); self.assertIn('非法工具调用', row['result'])
        models = [e['content']['model'] for e in events if e['kind'] == 'model_start']
        self.assertTrue(all(model == 'qwen3:latest' for model in models))

    def test_cancellation_terminates_deepeval_process(self):
        import subprocess
        original = subprocess.Popen; processes = []
        def spawn(*args, **kwargs):
            process = original(*args, **kwargs); processes.append(process); return process
        def respond(messages, **kwargs):
            if not kwargs['tools']:
                task = self.runtime.store.one("SELECT id FROM tasks WHERE status='running'")
                self.runtime.cancel(task['id'])
                return ChatResult.success('{"score":10,"reason":"通过"}')
            return ChatResult.success('候选结果')
        with patch('marvis.evaluation.subprocess.Popen', side_effect=spawn):
            row, events = self.run_task(respond)
        self.assertEqual(row['status'], 'cancelled'); self.assertTrue(processes)
        self.assertTrue(all(process.poll() is not None for process in processes))

    def test_prior_pass_cannot_certify_different_final_candidate(self):
        main_count = 0; judge_count = 0
        def respond(messages, **kwargs):
            nonlocal main_count, judge_count
            if not kwargs['tools']:
                judge_count += 1
                return ChatResult.success('{"score":10,"reason":"通过"}' if judge_count <= 3 else '{"score":1,"reason":"最终版本缺少成果"}')
            main_count += 1
            if main_count == 1: return dispatch_reply('evaluation', '评测初稿', '合格初稿')
            return ChatResult.success('不同的最终候选' + str(main_count))
        self.runtime.config['evaluation'] = {'max_repairs': 0}
        row, events = self.run_task(respond)
        self.assertEqual(row['status'], 'failed'); self.assertEqual(judge_count, 6)
        self.assertFalse(any(e['kind'] == 'delivery_accepted' for e in events))

    def test_unchanged_candidate_reuses_actual_sdk_receipt(self):
        main_count = 0; judge_count = 0
        def respond(messages, **kwargs):
            nonlocal main_count, judge_count
            if not kwargs['tools']:
                judge_count += 1; return ChatResult.success('{"score":10,"reason":"通过"}')
            main_count += 1
            return dispatch_reply('evaluation', '评测交付', '合格成果') if main_count == 1 else ChatResult.success('合格成果')
        row, events = self.run_task(respond)
        self.assertEqual(row['status'], 'completed'); self.assertEqual(judge_count, 3)
        self.assertEqual(sum(e['kind'] == 'delivery_accepted' for e in events), 1)

    def test_timeout_reaps_unresponsive_evaluation_process(self):
        import subprocess
        import sys
        interpreter = self.root / 'slow-python'
        interpreter.write_text('#!' + sys.executable + '\nimport time\ntime.sleep(30)\n')
        interpreter.chmod(0o755)
        self.runtime.config['evaluation'] = {'python': str(interpreter), 'timeout': 10}
        processes = []; original = subprocess.Popen
        def spawn(*args, **kwargs):
            process = original(*args, **kwargs); processes.append(process); return process
        with patch('marvis.evaluation.subprocess.Popen', side_effect=spawn):
            row, events = self.run_task(lambda m, **kw: ChatResult.success('候选结果'))
        self.assertEqual(row['status'], 'failed'); self.assertIn('超时', row['result'])
        self.assertTrue(all(process.poll() is not None for process in processes))
