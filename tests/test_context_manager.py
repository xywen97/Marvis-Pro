import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from llm_client import ChatResult
from marvis.context_manager import ContextPolicy, estimate_tokens, encoded
from marvis.runtime import Runtime, Cancelled
from marvis.store import uid
from marvis.transcript import process_nodes
from marvis.coordinator import Coordinator
from marvis.capabilities import Capabilities
from agent_fixture import report_reply


SUMMARY = '目标与约束：继续当前任务；拒绝删除。\n决策：保留现有接口。\n已验证证据：文件已读取，测试尚未运行。\n未完成与下一步：运行测试。'


class ContextManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / 'data'
        self.config = {'evaluation': {'enabled': False}, 'context_management': {
            'window_tokens': 6000, 'output_reserve_tokens': 512, 'safety_buffer_tokens': 256,
            'summary_tokens': 256, 'chunk_tokens': 2000, 'recent_units': 1}}
        self.runtime = Runtime(self.directory, config=self.config, offline=True)
        self.sid = self.runtime.store.session('context test')
        self.ident = uid()
        self.runtime.store.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)',
            (self.ident, self.sid, 'current', 'completed', '', time.time(), time.time()))
        self.runtime.cancels[self.ident] = threading.Event()
        self.anchor = self.runtime.store.add_message(self.sid, 'user', 'current', self.ident)
        self.client = SimpleNamespace(model='test-model', chat=Mock(return_value=ChatResult.success(SUMMARY)))

    def tearDown(self):
        self.runtime.close(); self.temp.cleanup()

    def messages(self):
        return [{'role': 'system', 'content': 'IMMUTABLE RULES'},
                {'role': 'user', 'content': 'old goal: do not delete'},
                {'role': 'assistant', 'content': 'old evidence ' * 1500},
                {'role': 'user', 'content': 'current precise input'},
                {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'id': 'recent', 'type': 'function', 'function': {'name': 'file_read', 'arguments': '{}'}}]},
                {'role': 'tool', 'tool_call_id': 'recent', 'content': '{"text":"recent evidence"}'}]

    def prepare(self, messages, actor='main', **kwargs):
        self.runtime.context_manager.prepare(self.ident, self.client, messages, [], actor, **kwargs)

    def checkpoints(self):
        return [e for e in self.runtime.store.events(self.ident) if e['kind'] == 'context_compaction']

    def messages_at_tokens(self, count):
        messages = self.messages(); messages[2]['content'] = ''
        size = len(encoded({'messages': messages, 'tools': []}).encode('utf-8'))
        messages[2]['content'] = 'x' * (count * 2 - size)
        self.assertEqual(estimate_tokens(messages), count)
        return messages

    def test_auto_window_defaults_and_early_overrides(self):
        self.assertEqual(ContextPolicy.load({}, 'unknown-model').window_tokens, 256000)
        self.assertEqual(ContextPolicy.load({}, 'unknown-model').trigger, 256000)
        for window, trigger in ((200000, 200000), (256000, 256000), (512000, 512000),
                                (1000000, 967000)):
            policy = ContextPolicy.load({'context_management': {'window_tokens': window}}, 'model')
            self.assertEqual(policy.trigger, trigger)
        options = {'window_tokens': 1000000, 'auto_compact_window_tokens': 500000,
                   'models': {'earlier': {'auto_compact_window_tokens': 200000},
                              'auto': {'auto_compact_window_tokens': None},
                              'capped': {'auto_compact_window_tokens': 2000000}}}
        self.assertEqual(ContextPolicy.load({'context_management': options}, 'other').trigger, 500000)
        self.assertEqual(ContextPolicy.load({'context_management': options}, 'earlier').trigger, 200000)
        self.assertEqual(ContextPolicy.load({'context_management': options}, 'auto').trigger, 967000)
        self.assertEqual(ContextPolicy.load({'context_management': options}, 'capped').trigger, 1000000)
        for invalid in (0, -1, True, 'auto'):
            with self.assertRaises(ValueError):
                ContextPolicy.load({'context_management': {'auto_compact_window_tokens': invalid}}, 'x')

    def test_default_256k_and_1m_only_summarize_at_resolved_boundary(self):
        for window, trigger in ((256000, 256000), (1000000, 967000)):
            with self.subTest(window=window):
                self.config['context_management'] = {'window_tokens': window}
                self.client.chat.reset_mock()
                before = self.messages_at_tokens(trigger - 1)
                self.prepare(before)
                self.client.chat.assert_not_called()
                at = self.messages_at_tokens(trigger)
                self.prepare(at)
                self.assertIn(SUMMARY, at[1]['content'])
                self.assertEqual(self.checkpoints()[-1]['content']['stage'], 'summary')
                self.assertLess(estimate_tokens(at), trigger)
                if window == 1000000:
                    # Large windows must not fail at the former 32-fragment cap.
                    self.assertGreater(self.client.chat.call_count, 32)

    def test_output_reserve_does_not_shift_boundary_and_caps_actual_output(self):
        messages = self.messages_at_tokens(5999)
        result = self.runtime._model_turn(self.ident, self.client, messages, 1, tools=[])
        self.assertTrue(result.ok)
        self.assertEqual(self.client.chat.call_count, 1)
        self.assertEqual(self.client.chat.call_args.kwargs['max_tokens'], 1)
        self.assertEqual(self.checkpoints(), [])

    def test_threshold_reserves_and_model_overrides_include_tool_schemas(self):
        policy = ContextPolicy.load(self.config, 'test-model')
        self.assertEqual(policy.trigger, 6000)
        self.assertGreater(estimate_tokens([], [{'description': 'long schema' * 100}]), estimate_tokens([]))
        self.config['context_management']['models'] = {'small': {'window_tokens': 4000}}
        self.assertEqual(ContextPolicy.load(self.config, 'small').window_tokens, 4000)
        self.assertEqual(ContextPolicy.load(self.config, 'other').window_tokens, 6000)
        with self.assertRaises(ValueError): ContextPolicy.load({'context_management': {'window_tokens': -1}}, 'x')

    def test_below_threshold_does_not_call_or_checkpoint(self):
        messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'short'}]
        original = copy.deepcopy(messages); self.prepare(messages)
        self.assertEqual(messages, original); self.client.chat.assert_not_called()
        self.assertEqual(self.checkpoints(), [])

    def test_all_agents_compact_privately_and_preserve_current_protocol(self):
        for actor in ('main', 'file', 'computer', 'app', 'browser', 'search', 'coding', 'evaluation'):
            with self.subTest(actor=actor):
                messages = self.messages(); original = copy.deepcopy(messages)
                self.client.chat.reset_mock(); self.prepare(messages, actor)
                self.assertEqual(messages[0], original[0]); self.assertEqual(messages[-3:], original[-3:])
                self.assertIn(SUMMARY, messages[1]['content'])
                self.assertLess(estimate_tokens(messages), ContextPolicy.load(self.config, 'test-model').trigger)
                for call in self.client.chat.call_args_list:
                    self.assertEqual(call.kwargs['tools'], []); self.assertEqual(call.kwargs['tool_choice'], 'none')
                    self.assertEqual(json.loads(call.args[0][1]['content'])['target_agent'], actor)
                self.assertEqual(self.checkpoints()[-1]['content']['agent'], actor)
                self.assertNotIn('IMMUTABLE RULES', json.dumps(self.checkpoints()[-1]['content']['messages']))

    def test_micro_compaction_retains_recent_tools_and_avoids_summary(self):
        self.config['context_management']['old_tool_chars'] = 256
        messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'goal'}]
        for i in range(8):
            messages.extend([{'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': str(i), 'function': {'name': 'read', 'arguments': '{}'}}]},
                {'role': 'tool', 'tool_call_id': str(i), 'content': json.dumps({'text': 'z'*1500})}])
        original = copy.deepcopy(messages); self.prepare(messages)
        self.client.chat.assert_not_called()
        self.assertEqual(messages[-10:], original[-10:])
        self.assertTrue(json.loads(messages[3]['content'])['truncated'])
        self.assertEqual(self.checkpoints()[-1]['content']['stage'], 'micro')

    def test_failed_or_oversized_summary_is_not_persisted_or_applied(self):
        for response in (ChatResult.failure('unavailable'), ChatResult.success(''),
                         ChatResult.success('x'*2000), ChatResult.success('bad', tool_calls=[{'id': 'bad'}])):
            messages = self.messages(); original = copy.deepcopy(messages)
            self.client.chat.return_value = response
            with self.assertRaises(ValueError): self.prepare(messages)
            self.assertEqual(messages, original); self.assertEqual(self.checkpoints(), [])

    def test_cancellation_deadline_and_recursion_guard(self):
        self.runtime.cancels[self.ident].set()
        with self.assertRaises(Cancelled): self.prepare(self.messages())
        self.runtime.cancels[self.ident].clear()
        with self.assertRaises(TimeoutError): self.prepare(self.messages(), deadline=time.monotonic()-1)
        self.prepare(self.messages(), actor='context')
        self.client.chat.assert_not_called(); self.assertEqual(self.checkpoints(), [])

    def test_single_giant_current_input_is_not_silently_dropped(self):
        messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'x'*15000}]
        original = copy.deepcopy(messages)
        with self.assertRaisesRegex(ValueError, '当前输入'): self.prepare(messages)
        self.assertEqual(messages, original); self.client.chat.assert_not_called()

    def test_pending_tool_call_is_kept_and_never_summarized(self):
        messages = self.messages()[:-1]; self.prepare(messages)
        self.assertEqual(messages[-1]['tool_calls'][0]['id'], 'recent')

    def test_runtime_returns_to_same_actor_and_only_retries_context_errors_once(self):
        messages = self.messages()
        self.client.chat.side_effect = lambda ms, **kw: ChatResult.success(
            SUMMARY if '上下文管理助手' in ms[0]['content'] else 'continued')
        result = self.runtime._model_turn(self.ident, self.client, messages, 1, tools=[], agent='coding')
        self.assertEqual(result.content, 'continued')
        self.assertIn(SUMMARY, self.client.chat.call_args.args[0][1]['content'])
        process_nodes({'id': self.ident, 'status': 'completed', 'events': self.runtime.store.events(self.ident)})
        short = self.messages(); short[2]['content'] = 'old' * 1200
        retries = []
        def respond(ms, **kwargs):
            if '上下文管理助手' in ms[0]['content']: return ChatResult.success(SUMMARY)
            retries.append(ms)
            return ChatResult.failure('context_length_exceeded')
        self.client.chat.side_effect = respond
        response = self.runtime._model_turn(self.ident, self.client, short, 2, tools=[], agent='file')
        self.assertFalse(response.ok)
        self.assertEqual(len(retries), 2)

    def test_checkpoint_restart_navigation_fork_and_delete(self):
        messages = self.messages(); self.prepare(messages)
        projected = self.runtime.context_messages(self.sid)
        self.assertEqual(projected, messages[1:])
        checkpoint = self.runtime.store.entry_branch(self.sid)[-1]['id']
        self.runtime.cancels.pop(self.ident)
        fork = self.runtime.fork_session(self.sid, checkpoint)
        self.assertEqual(self.runtime.context_messages(fork['session_id']), projected)
        self.runtime.navigate_tree(self.sid, self.anchor)
        self.assertEqual(self.runtime.context_messages(self.sid), [])
        self.runtime.navigate_tree(self.sid, checkpoint)
        self.runtime.close()
        self.runtime = Runtime(self.directory, config=self.config, offline=True)
        self.assertEqual(self.runtime.context_messages(self.sid), projected)
        self.runtime.session_action(self.sid, 'delete')
        self.assertEqual(self.runtime.store.rows('SELECT * FROM entries WHERE session_id=?', (self.sid,)), [])
        self.assertEqual(self.runtime.context_messages(fork['session_id']), projected)

    def test_worker_checkpoint_is_excluded_from_main_projection(self):
        self.prepare(self.messages(), actor='file')
        self.assertEqual(self.runtime.context_messages(self.sid), [{'role': 'user', 'content': 'current'}])
        checkpoints = [n for n in self.runtime.store.entry_branch(self.sid) if n['kind']=='context_compaction']
        self.assertEqual(checkpoints[0]['data']['agent'], 'file')

    def test_worker_dispatch_restores_compacted_history_without_other_agent_memory(self):
        self.config['context_management']['window_tokens'] = 16000
        self.runtime.store.event(self.ident, 'agent_context', {'agent': 'file', 'messages': [
            {'role': 'user', 'content': 'file-only objective'},
            {'role': 'assistant', 'content': 'FILE_PRIVATE ' * 3000}]})
        self.runtime.store.event(self.ident, 'agent_context', {'agent': 'computer', 'messages': [
            {'role': 'assistant', 'content': 'COMPUTER_PRIVATE'}]})
        seen = []
        def respond(ms, **kwargs):
            self.assertNotIn('COMPUTER_PRIVATE', json.dumps(ms))
            if '上下文管理助手' in ms[0]['content']: return ChatResult.success(SUMMARY)
            seen.append(copy.deepcopy(ms)); return report_reply('已完成')
        self.client.chat.side_effect = respond
        settings = self.runtime.settings()
        coordinator = Coordinator(self.runtime, self.ident, self.sid, Capabilities(self.runtime.store), settings)
        with patch.object(self.runtime, 'model', return_value=self.client):
            coordinator.dispatch({'agent': 'file', 'objective': 'first file task', 'context': 'file input'})
            coordinator.dispatch({'agent': 'file', 'objective': 'next file task', 'context': 'new file input'})
        self.assertEqual(len(seen), 2)
        self.assertIn(SUMMARY, seen[0][1]['content']); self.assertIn(SUMMARY, seen[1][1]['content'])
        self.assertNotIn(SUMMARY, json.dumps(self.runtime.context_messages(self.sid)))

    def test_second_compaction_merges_previous_memory_and_continues(self):
        messages = self.messages(); self.prepare(messages)
        messages.extend([{'role': 'assistant', 'content': 'new evidence ' * 1500},
                         {'role': 'user', 'content': 'latest correction: keep all files'}])
        self.client.chat.reset_mock(); self.prepare(messages)
        self.assertEqual(messages[-1]['content'], 'latest correction: keep all files')
        self.assertEqual(len(self.checkpoints()), 2)
        requests = json.dumps([c.args[0] for c in self.client.chat.call_args_list], ensure_ascii=False)
        self.assertIn('历史压缩记忆', requests)


if __name__ == '__main__': unittest.main()
