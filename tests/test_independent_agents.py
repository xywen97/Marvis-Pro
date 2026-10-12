import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from llm_client import LLMClient, ChatResult
from marvis.agent_protocol import REQUEST_SCHEMA, TASK_SCHEMA, RESULT_SCHEMA, validate
from marvis.runtime import Runtime
from marvis.capabilities import Capabilities
from marvis.team import TOOL_AGENTS
from agent_fixture import dispatch_reply, report_reply, tool_reply


class IndependentAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.runtime = Runtime(self.root/'data', config={'evaluation': {'enabled': False}})
        self.runtime.update_settings({'roots': [str(self.root)]})

    def tearDown(self):
        self.runtime.close(); self.tmp.cleanup()

    def submit(self, respond, session=None, prompt='MAIN_PRIVATE 处理多个领域任务'):
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), patch.object(LLMClient, 'chat', side_effect=respond):
            task = self.runtime.submit(prompt, session)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                row = self.runtime.store.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
                if row['status'] in ('completed', 'failed', 'cancelled'): break
                time.sleep(.01)
            self.assertEqual(row['status'], 'completed', row['result'])
        return task

    @staticmethod
    def main(kwargs):
        return 'agent_dispatch' in {t['function']['name'] for t in kwargs['tools']}

    def test_context_isolation_role_tools_and_same_agent_continuity(self):
        main_turns = []; workers = []
        sequence = ['file', 'computer', 'file']
        def respond(messages, **kwargs):
            if self.main(kwargs):
                main_turns.append(json.loads(json.dumps(messages)))
                index = len(main_turns) - 1
                return dispatch_reply(sequence[index], '阶段' + str(index), '仅必要输入') if index < 3 else ChatResult.success('已汇总')
            request = next(json.loads(m['content']) for m in reversed(messages) if m['role'] == 'user')
            validate(request, TASK_SCHEMA)
            actor = request['agent']; names = {t['function']['name'] for t in kwargs['tools']}
            self.assertNotIn('agent_dispatch', names)
            self.assertTrue(all(TOOL_AGENTS.get(n) == actor for n in names - {'agent_report'}))
            raw = json.dumps(messages, ensure_ascii=False)
            self.assertNotIn('MAIN_PRIVATE', raw)
            if actor == 'computer': self.assertNotIn('FILE_PRIVATE', raw)
            if len(workers) == 2: self.assertIn('FILE_PRIVATE', raw); self.assertNotIn('COMPUTER_PRIVATE', raw)
            workers.append(actor)
            return tool_reply('agent_report', {'status': 'completed', 'summary': '公开报告', 'requests': []}, actor.upper() + '_PRIVATE')
        task = self.submit(respond)
        self.assertEqual(workers, sequence)
        self.assertNotIn('FILE_PRIVATE', json.dumps(main_turns, ensure_ascii=False))
        results = [e['content']['result'] for e in self.runtime.store.events(task['id']) if e['kind'] == 'agent_result']
        for result in results: validate(result, RESULT_SCHEMA)
        self.assertEqual([r['agent'] for r in results], sequence)

    def test_coding_has_shared_tools_private_context_and_real_evidence(self):
        path = self.root / 'code.py'; path.write_text('answer = 42\n')
        count = 0
        def respond(messages, **kwargs):
            nonlocal count
            names = {t['function']['name'] for t in kwargs['tools']}
            if self.main(kwargs):
                return ChatResult.success('已核实') if messages[-1]['role'] == 'tool' else dispatch_reply('coding', '检查代码', str(path))
            self.assertIn('file_edit', names); self.assertIn('code_check', names)
            self.assertNotIn('system_info', names); self.assertNotIn('agent_dispatch', names)
            self.assertNotIn('MAIN_PRIVATE', json.dumps(messages))
            count += 1
            if count == 1: return tool_reply('file_read_lines', {'path': str(path)})
            if count == 2: return tool_reply('code_check', {'path': str(path)})
            return report_reply('语法已核实')
        task = self.submit(respond)
        events = self.runtime.store.events(task['id'])
        results = [e['content'] for e in events if e['kind'] == 'tool_result' and e['content'].get('scope') == 'worker']
        self.assertEqual([r['agent'] for r in results], ['coding', 'coding'])
        result = next(e['content']['result'] for e in events if e['kind'] == 'agent_result')
        validate(result, RESULT_SCHEMA)
        self.assertEqual(result['agent'], 'coding'); self.assertEqual(len(result['evidence']), 2)

    def test_handoff_is_returned_to_main_and_only_main_bridges_it(self):
        stages = []; request_ids = []
        def respond(messages, **kwargs):
            if self.main(kwargs):
                stages.append('main')
                results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
                if not results: return dispatch_reply('computer', '检查并保存')
                if len(results) == 1:
                    self.assertEqual(results[0]['status'], 'needs_handoff')
                    item = results[0]['requests'][0]; validate(item, REQUEST_SCHEMA)
                    request_ids.append(results[0]['request_id'])
                    return tool_reply('agent_dispatch', {**item, 'continuation_of': results[0]['request_id']})
                return ChatResult.success('由主控完成转接')
            actor = json.loads(messages[-1]['content'])['agent']; stages.append(actor)
            if actor == 'computer':
                return report_reply('需要保存', 'needs_handoff', [{'agent': 'file', 'objective': '保存报告', 'context': '指标证据'}])
            return report_reply('已接到文件任务')
        self.submit(respond)
        self.assertEqual(stages, ['main', 'computer', 'main', 'file', 'main'])

    def test_handoff_does_not_autorun_when_main_declines(self):
        workers = []
        def respond(messages, **kwargs):
            if self.main(kwargs):
                return ChatResult.success('停止转接') if messages[-1]['role'] == 'tool' else dispatch_reply()
            workers.append('computer')
            return report_reply('需要文件助手', 'needs_handoff', [{'agent': 'file', 'objective': '写文件', 'context': ''}])
        self.submit(respond); self.assertEqual(workers, ['computer'])

    def test_worker_cannot_dispatch_or_execute_foreign_tool(self):
        count = 0
        def respond(messages, **kwargs):
            nonlocal count
            if self.main(kwargs): return ChatResult.success('已停止') if messages[-1]['role'] == 'tool' else dispatch_reply()
            count += 1
            if count == 1: return dispatch_reply('file', '越权请求')
            if count == 2: return tool_reply('file_write', {'path': str(self.root/'bad.txt'), 'text': 'bad'})
            return report_reply('越权工具被阻止', 'partial')
        task = self.submit(respond)
        self.assertFalse((self.root/'bad.txt').exists())
        self.assertEqual(sum(e['kind'] == 'agent_start' for e in self.runtime.store.events(task['id'])), 1)
        errors = [e['content']['text'] for e in self.runtime.store.events(task['id']) if e['kind'] == 'tool_error']
        self.assertEqual(sum('其他 Agent' in text for text in errors), 2)

    def test_invalid_report_and_self_handoff_are_rejected(self):
        count = 0
        def respond(messages, **kwargs):
            nonlocal count
            if self.main(kwargs): return ChatResult.success('已核实') if messages[-1]['role'] == 'tool' else dispatch_reply()
            count += 1
            if count == 1: return tool_reply('agent_report', {'status': 'completed', 'summary': 'bad'})
            if count == 2: return report_reply('self', 'needs_handoff', [{'agent': 'computer', 'objective': '循环', 'context': ''}])
            return report_reply('报告协议正确')
        task = self.submit(respond)
        errors = [e['content']['text'] for e in self.runtime.store.events(task['id']) if e['kind'] == 'tool_error']
        self.assertTrue(any('必填' in s for s in errors)); self.assertTrue(any('自己' in s for s in errors))

    def test_repeated_dispatch_and_global_budget_bound_cycles(self):
        self.runtime.config['orchestration'] = {'max_rounds': 8, 'max_dispatches': 3}
        counts = {'main': 0, 'worker': 0}
        def respond(messages, **kwargs):
            if not self.main(kwargs): counts['worker'] += 1; return report_reply()
            counts['main'] += 1
            if counts['main'] <= 3: return dispatch_reply(objective='重复')
            if counts['main'] <= 5: return dispatch_reply(objective='变化' + str(counts['main']))
            return ChatResult.success('预算已限制转接')
        task = self.submit(respond)
        self.assertEqual(counts['worker'], 3)
        errors = [e['content']['text'] for e in self.runtime.store.events(task['id']) if e['kind'] == 'tool_error']
        self.assertTrue(any('循环' in s for s in errors)); self.assertTrue(any('调度上限' in s for s in errors))

    def test_local_mode_worker_whitelist_and_disabled_browser(self):
        self.runtime.update_settings({'mode': 'local'}); workers = []
        main_count = 0
        def respond(messages, **kwargs):
            nonlocal main_count
            if self.main(kwargs):
                main_count += 1
                if main_count == 1: return dispatch_reply('search', '查询资料')
                if main_count == 2: return dispatch_reply('browser', '读取网页')
                return ChatResult.success('本地模式限制已说明')
            names = {t['function']['name'] for t in kwargs['tools']}
            self.assertEqual(names, {'knowledge_search', 'agent_report'}); workers.append('search')
            return report_reply()
        self.submit(respond); self.assertEqual(workers, ['search'])

    def test_shared_model_budget_cannot_reset_for_another_worker(self):
        self.runtime.config['orchestration'] = {'max_model_calls': 2}
        calls = []
        def respond(messages, **kwargs):
            calls.append(self.main(kwargs))
            return dispatch_reply() if self.main(kwargs) else report_reply('实际已提交报告')
        task = self.submit(respond)
        self.assertEqual(calls, [True, False])
        row = self.runtime.store.one('SELECT result FROM tasks WHERE id=?', (task['id'],))
        self.assertIn('共享调用上限', row['result']); self.assertIn('实际已提交报告', row['result'])

    def test_terminal_report_cannot_hide_same_batch_execution(self):
        count = 0
        def respond(messages, **kwargs):
            nonlocal count
            if self.main(kwargs): return ChatResult.success('批次已阻断') if messages[-1]['role'] == 'tool' else dispatch_reply()
            count += 1
            if count == 1:
                result = tool_reply('system_info', {'section': 'disk'})
                result.tool_calls += report_reply().tool_calls
                return result
            return report_reply('不符合协议的批次没有执行', 'partial')
        with patch.object(Capabilities, 'execute') as execute:
            self.submit(respond)
            execute.assert_not_called()

    def test_main_cannot_execute_worker_tool_directly(self):
        count = 0
        def respond(messages, **kwargs):
            nonlocal count
            count += 1
            return tool_reply('system_info', {}) if count == 1 else ChatResult.success('主控越权调用被拒绝')
        with patch.object(Capabilities, 'execute') as execute:
            self.submit(respond); execute.assert_not_called()

    def test_private_context_survives_restart_and_branch_copy_without_other_branch_leak(self):
        observed = []; main_turn = 0
        def respond(messages, **kwargs):
            nonlocal main_turn
            if self.main(kwargs):
                main_turn += 1
                return dispatch_reply('file', '阶段' + str(main_turn)) if messages[-1]['role'] == 'user' else ChatResult.success('完成')
            observed.append(json.dumps(messages, ensure_ascii=False))
            return tool_reply('agent_report', {'status': 'completed', 'summary': '报告', 'requests': []}, 'PRIVATE_MARK_' + str(len(observed)))
        first = self.submit(respond)
        anchor = self.runtime.store.entry_branch(first['session_id'])[-1]['id']
        self.submit(respond, first['session_id'])
        self.assertIn('PRIVATE_MARK_1', observed[-1])
        copied = self.runtime.fork_session(first['session_id'], anchor)['session_id']
        self.runtime.close(); self.runtime = Runtime(self.root/'data', config={'evaluation': {'enabled': False}})
        self.submit(respond, copied)
        self.assertIn('PRIVATE_MARK_1', observed[-1]); self.assertNotIn('PRIVATE_MARK_2', observed[-1])
        self.runtime.navigate_tree(first['session_id'], anchor)
        self.submit(respond, first['session_id'])
        self.assertNotIn('PRIVATE_MARK_2', observed[-1])


if __name__ == '__main__': unittest.main()
