import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from llm_client import ChatResult, LLMClient
from marvis.capabilities import Capabilities, TOOLS, PageText, SearchResults, validate_arguments
from marvis.runtime import Runtime
from marvis.team import AGENTS, TOOL_AGENTS, tool_message


class AgentContractsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name).resolve()
        self.work = self.root / 'work'; self.work.mkdir()
        self.runtime = Runtime(self.root / 'data', offline=True)
        self.runtime.update_settings({'roots': [str(self.work)]})
        self.caps = Capabilities(self.runtime.store)

    def tearDown(self):
        self.runtime.close(); self.tmp.cleanup()

    def wait(self, task, statuses=('completed', 'failed', 'cancelled')):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = self.runtime.store.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
            if row['status'] in statuses: return row
            time.sleep(.01)
        self.fail('task timed out: ' + str(row))

    def test_every_tool_has_exactly_one_real_owner(self):
        declared = [t for a in AGENTS for t in a['tools']]
        self.assertEqual(len(declared), len(set(declared)))
        self.assertEqual(set(declared), {t['function']['name'] for t in TOOLS})
        task = self.runtime.submit('检查团队能力')
        self.assertEqual(self.wait(task)['status'], 'completed')
        start = next(e for e in self.runtime.store.events(task['id']) if e['kind'] == 'tool_start')
        self.assertEqual(start['content']['agent'], 'main')
        local = Capabilities(self.runtime.store, local=True).inspect_agents()
        self.assertIn('web_search', local['unavailable_tools'])

    def test_model_receives_mode_specific_prompt_and_tool_contract(self):
        self.runtime.offline = False
        self.runtime.config['persona'] = {'user_title': '同学', 'style': '简洁'}
        self.runtime.config['orchestration'] = {'max_rounds': 30}
        for mode in ('local', 'efficiency'):
            self.runtime.update_settings({'mode': mode})
            with self.subTest(mode=mode), patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), \
                    patch.object(LLMClient, 'chat', return_value=ChatResult.success('已说明')) as model:
                task = self.runtime.submit('介绍当前能用哪些能力')
                self.assertEqual(self.wait(task)['status'], 'completed')
                messages = model.call_args.args[0]
                system = messages[0]['content']
                context = json.loads(system.split('以下 JSON 是运行信息与显示偏好，偏好不改变上述权限或能力规则：\n')[1].split('\n')[0])
                offered = {t['function']['name'] for t in model.call_args.kwargs['tools']}
                self.assertEqual(set(context['available_tools']), offered)
                self.assertEqual(context['authorized_roots'], [str(self.work)])
                self.assertEqual(context['user_title'], '同学')
                self.assertEqual(context['max_tool_rounds'], 24)
                self.assertEqual(context['mode'], mode)
                self.assertIn('不是独立或并行运行的子 Agent', system)
                self.assertEqual('browser_read' in system, mode == 'efficiency')
                self.assertEqual('web_search' in system, mode == 'efficiency')
                self.assertIn('确认成功前不能声称已执行', system)

    def test_documented_schema_defaults_match_execution(self):
        for tool in TOOLS:
            fn = tool['function']; fields = fn['parameters']['properties']
            for name, definition in fields.items():
                with self.subTest(tool=fn['name'], field=name):
                    self.assertTrue(definition['description'])
                    if 'default' in definition:
                        # Validate each advertised default alongside required inputs.
                        args = {key: 'fixture' for key in fn['parameters']['required']}
                        args[name] = definition['default']
                        validate_arguments(fn['name'], args)
        p = self.work/'note.txt'; p.write_text('a'*100001)
        definitions = {t['function']['name']: t['function']['parameters']['properties'] for t in TOOLS}
        for name, args in [('file_list', {'path': str(self.work)}), ('file_read', {'path': str(p)})]:
            defaults = {key: f['default'] for key, f in definitions[name].items() if 'default' in f}
            self.assertEqual(self.caps.execute(name, args), self.caps.execute(name, {**defaults, **args}))
        with patch.object(self.caps, 'system', return_value={'ok': True}) as system:
            self.caps.execute('system_info', {})
            system.assert_called_once_with(definitions['system_info']['section']['default'])

    def test_prompted_read_loop_preserves_real_pagination_evidence(self):
        self.runtime.offline = False
        p = self.work/'note.txt'; p.write_text('甲乙丙丁')
        observed = []
        def respond(messages, **kwargs):
            results = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
            if len(results) == 2:
                observed.extend(results)
                return ChatResult.success('读取完整内容：' + ''.join(r['text'] for r in results))
            offset = results[-1]['next_offset'] if results else 0
            return ChatResult.success('', tool_calls=[{'id': 'page-' + str(offset), 'function': {
                'name': 'file_read', 'arguments': json.dumps({'path': str(p), 'offset': offset, 'limit': 2})}}])
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), patch.object(LLMClient, 'chat', side_effect=respond):
            task = self.runtime.submit('请完整读取这份笔记并汇总')
            self.assertEqual(self.wait(task)['status'], 'completed')
        self.assertEqual([r['text'] for r in observed], ['甲乙', '丙丁'])
        self.assertTrue(observed[0]['has_more']); self.assertFalse(observed[1]['has_more'])

    def test_shortcuts_do_not_execute_negation_advice_or_partial_work(self):
        for prompt in ('不要整理文件', '如何整理文件', '分析磁盘和内存', '将磁盘信息保存为报告', '是否打开 https://example.com'):
            with self.subTest(prompt=prompt): self.assertIsNone(Runtime.direct(prompt, [str(self.work)]))
        self.assertEqual(Runtime.direct('检查磁盘空间', [str(self.work)])['tool'], 'system_info')

    def test_invalid_arguments_fail_before_preview_or_execution(self):
        for name, args in [('file_read', {}), ('file_list', {'path': str(self.work), 'content': 'true'}),
                           ('system_info', {'section': 'invalid'}), ('app_list', {'extra': 1}),
                           ('file_list', {'path': str(self.work), 'limit': True}),
                           ('file_read', {'path': 'x', 'offset': -1}), ('knowledge_search', {'query': ' '})]:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError): validate_arguments(name, args)
        validate_arguments('file_list', {'path': str(self.work), 'query': ''})
        task = self.runtime.submit('bad', action={'tool': 'file_write', 'arguments': {'path': str(self.work/'x')}})
        self.assertEqual(self.wait(task)['status'], 'failed')
        self.assertFalse((self.work/'x').exists())
        kinds = [e['kind'] for e in self.runtime.store.events(task['id'])]
        self.assertIn('tool_error', kinds); self.assertNotIn('approval', kinds)
        context = self.runtime.context_messages(task['session_id'])
        self.assertFalse(any('No result provided' in m.get('content', '') for m in context))

    def test_file_search_pagination_and_large_text_windows(self):
        for i in range(7): (self.work / f'{i}.txt').write_text('内容')
        first = self.caps.execute('file_list', {'path': str(self.work), 'limit': 3})
        second = self.caps.execute('file_list', {'path': str(self.work), 'limit': 3, 'offset': first['next_offset']})
        last = self.caps.execute('file_list', {'path': str(self.work), 'limit': 3, 'offset': second['next_offset']})
        self.assertEqual([len(x['files']) for x in (first, second, last)], [3, 3, 1])
        self.assertFalse(last['has_more'])
        text = '甲' * 100000 + '尾部内容'; p = self.work/'large.txt'; p.write_text(text)
        result = self.caps.execute('file_read', {'path': str(p), 'offset': 99998, 'limit': 3})
        self.assertEqual(result['text'], text[99998:100001]); self.assertTrue(result['has_more'])
        result = self.caps.execute('file_read', {'path': str(p), 'offset': result['next_offset']})
        self.assertEqual(result['text'], text[100001:]); self.assertFalse(result['has_more'])

    def test_incremental_index_only_reads_changed_documents(self):
        p = self.work/'note.md'; p.write_text('a'*500+'目标内容')
        self.caps.execute('knowledge_index', {'path': str(self.work)})
        with patch.object(self.caps, 'read', wraps=self.caps.read) as read:
            result = self.caps.execute('knowledge_index', {'path': str(self.work)})
            self.assertEqual(result['unchanged'], 1); read.assert_not_called()
            p.write_text('a'*500+'目标内容已更新')
            result = self.caps.execute('knowledge_index', {'path': str(self.work)})
            self.assertEqual(result['indexed'], 1); self.assertEqual(read.call_count, 1)
        result = self.caps.execute('knowledge_search', {'query': '目标'})
        self.assertIn('目标', result['files'][0]['preview']); self.assertFalse(result['files'][0]['stale'])
        p.write_text('changed')
        self.assertTrue(self.caps.execute('knowledge_search', {'query': '目标'})['files'][0]['stale'])
        p.unlink()
        self.assertEqual(self.caps.execute('knowledge_search', {'query': '目标'})['files'], [])

    def test_search_does_not_let_revoked_roots_hide_allowed_matches(self):
        outside = self.root/'outside'; outside.mkdir()
        self.runtime.update_settings({'roots': [str(outside), str(self.work)]})
        for i in range(105): (outside/f'needle{i}.md').write_text('needle')
        (self.work/'needle.md').write_text('needle')
        self.caps.execute('knowledge_index', {'path': str(self.work)})
        self.caps.execute('knowledge_index', {'path': str(outside)})
        self.runtime.update_settings({'roots': [str(self.work)]})
        result = self.caps.execute('knowledge_search', {'query': 'needle'})
        self.assertEqual(len(result['files']), 1)

    def test_conversion_reads_entire_csv_and_checks_approved_snapshot(self):
        p = self.work/'data.csv'; p.write_text('value\n'+'1234567890\n'*10001)
        args = {'path': str(p), 'format': 'json'}; plan = self.caps.preview('document_convert', args)
        self.caps.execute('document_convert', args, plan)
        self.assertEqual(len(json.loads((self.work/'data.json').read_text())), 10001)
        (self.work/'data.json').unlink(); plan = self.caps.preview('document_convert', args)
        p.write_text('value\nchanged\n')
        with self.assertRaisesRegex(ValueError, '已变化'): self.caps.execute('document_convert', args, plan)
        self.assertFalse((self.work/'data.json').exists())
        with self.assertRaisesRegex(ValueError, '不支持'): self.caps.preview('document_convert', {'path': str(p), 'format': 'csv'})

    def test_page_and_search_parsers_preserve_evidence(self):
        parser = PageText(); parser.feed('<title>标题</title><script>secret()</script><style>hidden</style><p>正文 &amp; 内容</p>')
        self.assertEqual(parser.title_parts, ['标题']); self.assertEqual(parser.parts, ['正文 & 内容'])
        parser = SearchResults(); parser.feed('<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com" class="other result__a"><b>例子</b> &amp; 来源</a>'
            '<a class="result__a" href="https://example.com">duplicate</a><a class="result__a" href="javascript:bad()">bad</a>')
        self.assertEqual(parser.results, [{'title': '例子 & 来源', 'url': 'https://example.com'}])
        with patch.object(self.caps, 'fetch', return_value='<form id="challenge-form">captcha</form>'), self.assertRaisesRegex(ValueError, '人机验证'):
            self.caps.execute('web_search', {'query': 'test'})

    def test_app_discovery_cache_prunes_bundles(self):
        old = Capabilities._app_cache; Capabilities._app_cache = None
        visited = []
        def walk(base):
            dirs = ['Safari.app', 'Utilities', '.hidden']; yield str(base), dirs, []
            visited.append(list(dirs))
            yield str(base/'Utilities'), ['工具.app'], []
        try:
            with patch('marvis.capabilities.sys.platform', 'darwin'), patch('marvis.capabilities.os.walk', side_effect=walk) as scan:
                self.assertEqual(self.caps.app_names(), ['Safari', '工具'])
                self.caps.app_names(); self.assertEqual(scan.call_count, 3)
                self.assertTrue(all(d == ['Utilities'] for d in visited))
        finally: Capabilities._app_cache = old

    def test_directory_scan_caches_roots_but_revocation_is_immediate(self):
        for i in range(150): (self.work/f'{i}.md').write_text('text')
        with patch.object(self.runtime.store, 'setting', wraps=self.runtime.store.setting) as setting:
            self.caps.execute('file_list', {'path': str(self.work)})
            roots_calls = [c for c in setting.call_args_list if c.args[0] == 'roots']
            self.assertEqual(len(roots_calls), 1)
        outside = self.root/'outside'; outside.mkdir()
        self.runtime.update_settings({'roots': [str(outside)]})
        with self.assertRaises(PermissionError): self.caps.read(str(self.work/'0.md'))

    def test_duplicate_sampling_never_confuses_different_middle_bytes(self):
        prefix, suffix = b'a'*65536, b'z'*65536
        (self.work/'a.bin').write_bytes(prefix+b'one'+suffix)
        (self.work/'b.bin').write_bytes(prefix+b'two'+suffix)
        (self.work/'c.bin').write_bytes(prefix+b'one'+suffix)
        (self.work/'d.bin').write_bytes(b'different'+b'x'*(len(prefix)+len(suffix)-6))
        result = self.caps.execute('file_duplicates', {'path': str(self.work)})
        self.assertEqual(result['duplicates'], [[str(self.work/'a.bin'), str(self.work/'c.bin')]])
        self.assertEqual(result['fully_hashed'], 3)

    def test_computer_metrics_preserve_raw_evidence(self):
        with patch('marvis.capabilities.sys.platform', 'darwin'), patch.object(self.caps, 'command', return_value='Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 64.\nPages active: 128.'):
            result = self.caps.execute('system_info', {'section': 'memory'})
            self.assertEqual(result['metrics_mb']['Pages free'], 1.0)
            self.assertIn('Pages active: 128.', result['output'])
        with patch.object(self.caps, 'command', return_value='PID %CPU %MEM COMM\n1 0.5 2 /path with spaces\n2 35 3 /cpu'):
            result = self.caps.execute('system_info', {'section': 'processes'})
            self.assertEqual(result['top_processes'][0]['pid'], 2)
            self.assertEqual(result['top_processes'][1]['command'], '/path with spaces')
        with patch('marvis.capabilities.sys.platform', 'darwin'), patch.object(self.caps, 'command', return_value="Now drawing from 'Battery Power'\nBattery-0 42%; discharging"):
            result = self.caps.execute('system_info', {'section': 'battery'})
            self.assertEqual(result['charge_percent'], 42); self.assertEqual(result['power_source'], 'battery')

    def test_app_name_resolution_and_ocr_language_fallback(self):
        with patch('marvis.capabilities.sys.platform', 'darwin'), patch.object(self.caps, 'app_names', return_value=['Safari']), patch.object(self.caps, 'command', return_value='') as command:
            args = {'name': 'safari'}; plan = self.caps.preview('app_open', args)
            result = self.caps.execute('app_open', args, plan)
            self.assertEqual(result['opened'], 'Safari'); command.assert_called_once_with(['open', '-a', 'Safari'])
        p = self.work/'image.png'; p.write_bytes(b'fixture')
        with patch('marvis.capabilities.shutil.which', return_value='/bin/tesseract'), patch.object(self.caps, 'command', side_effect=['List of available languages (1):\neng', 'OCR text']) as command:
            result = self.caps.execute('image_ocr', {'path': str(p)})
            self.assertEqual(result['language'], 'eng'); self.assertIn('中文语言包', result['notice'])
            self.assertEqual(command.call_args.args[0][-1], 'eng')

    def test_organize_unlink_failure_removes_created_hardlink(self):
        p = self.work/'a.txt'; p.write_text('keep'); args = {'path': str(self.work)}
        plan = self.caps.preview('file_organize', args); unlink = Path.unlink
        def fail_source(path, *a, **kw):
            if path == p: raise PermissionError('source busy')
            return unlink(path, *a, **kw)
        with patch.object(Path, 'unlink', fail_source), self.assertRaises(PermissionError): self.caps.execute('file_organize', args, plan)
        self.assertTrue(p.exists()); self.assertFalse((self.work/'文档/a.txt').exists())

    def test_tool_projection_is_valid_bounded_json(self):
        result = {'files': [{'path': '/tmp/'+str(i), 'text': '字"\\'*10000} for i in range(50)]}
        raw = tool_message(result)
        self.assertLessEqual(len(raw), 24000); self.assertTrue(json.loads(raw)['truncated'])
        self.assertEqual(len(result['files']), 50)

    def test_context_pruning_keeps_complete_protocol_and_current_goal(self):
        messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'old'},
                    {'role': 'assistant', 'content': 'x'*9000}, {'role': 'user', 'content': 'current'},
                    {'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'c', 'function': {'name': 'file_read', 'arguments': '{}'}}]},
                    {'role': 'tool', 'tool_call_id': 'c', 'content': json.dumps({'text': 'y'*20000})}]
        projected = Runtime.bounded_context(messages, 5000)
        self.assertLessEqual(len(json.dumps(projected, ensure_ascii=False)), 5000)
        self.assertEqual([m['content'] for m in projected if m['role'] == 'user'], ['current'])
        self.assertEqual(projected[-1]['tool_call_id'], projected[-2]['tool_calls'][0]['id'])

    def test_rejected_tool_cannot_reprompt_with_changed_arguments(self):
        self.runtime.offline = False
        calls = [{'id': 'same', 'type': 'function', 'function': {'name': 'file_write', 'arguments': json.dumps({'path': str(self.work/f'{i}.txt'), 'text': 'value'})}} for i in range(2)]
        replies = [ChatResult.success('', tool_calls=calls), ChatResult.success('已停止')]
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), patch.object(LLMClient, 'chat', side_effect=replies) as model:
            task = self.runtime.submit('帮忙创建两份笔记')
            self.wait(task, ('waiting_approval',)); self.runtime.approve(task['id'], False)
            self.assertEqual(self.wait(task)['status'], 'completed')
        events = self.runtime.store.events(task['id'])
        self.assertEqual(sum(e['kind'] == 'approval' for e in events), 1)
        self.assertEqual(sum(e['kind'] == 'rejected' for e in events), 2)
        self.assertEqual(list(self.work.iterdir()), [])
        sent = model.call_args.args[0]
        ids = [m['tool_call_id'] for m in sent if m['role'] == 'tool']
        self.assertEqual(len(set(ids)), 2)

    def test_repeated_failures_stop_executing_and_local_tools_filtered(self):
        self.runtime.offline = False; self.runtime.update_settings({'mode': 'local'})
        replies = [ChatResult.success('', tool_calls=[{'id': str(i), 'function': {'name': 'system_info', 'arguments': '{"section":"disk"}'}}]) for i in range(3)] + [ChatResult.success('失败原因')]
        with patch.object(LLMClient, 'chat', side_effect=replies) as model, patch.object(Capabilities, 'execute', side_effect=ValueError('test failure')) as execute:
            task = self.runtime.submit('帮忙评估电脑健康')
            self.assertEqual(self.wait(task)['status'], 'completed'); self.assertEqual(execute.call_count, 2)
        offered = {t['function']['name'] for t in model.call_args.kwargs['tools']}
        self.assertNotIn('web_search', offered); self.assertIn('agent_inspect', offered)
        self.assertIn('失败两次', json.dumps(self.runtime.store.events(task['id']), ensure_ascii=False))

    def test_execution_budget_does_not_execute_final_provider_calls(self):
        self.runtime.offline = False; self.runtime.config['orchestration'] = {'max_rounds': 1}
        result = ChatResult.success('', tool_calls=[{'id': 'c', 'function': {'name': 'system_info', 'arguments': '{}'}}])
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), patch.object(LLMClient, 'chat', return_value=result), patch.object(Capabilities, 'execute', return_value={'ok': True}) as execute:
            task = self.runtime.submit('帮忙全面评估电脑')
            self.assertEqual(self.wait(task)['status'], 'failed'); self.assertEqual(execute.call_count, 1)

    def test_empty_model_response_is_failure_not_success(self):
        self.runtime.offline = False
        with patch.object(LLMClient, 'configured', new_callable=lambda: property(lambda s: True)), patch.object(LLMClient, 'chat', return_value=ChatResult.success('')):
            task = self.runtime.submit('请给我一些建议')
            self.assertEqual(self.wait(task)['status'], 'failed')

    def test_model_response_size_limits(self):
        client = LLMClient(base_url='http://127.0.0.1/v1', api_key='test', model='test', env_file='/nonexistent')
        for streaming in (False, True):
            response = MagicMock(); response.__enter__.return_value = response
            response.headers = {'Content-Type': 'text/event-stream' if streaming else 'application/json'}
            response.read.return_value = b'x'*(4*1024*1024+1)
            response.__iter__.return_value = iter([b':'*(8*1024*1024+1)])
            opener = MagicMock(); opener.open.return_value = response
            with patch('llm_client.urllib.request.build_opener', return_value=opener):
                result = client.chat([{'role': 'user', 'content': 'test'}], on_delta=(lambda _: None) if streaming else None)
            self.assertFalse(result.ok); self.assertIn('MB 限制', result.error)

    def test_document_limits_prevent_incomplete_conversion(self):
        try:
            from openpyxl import Workbook
            from pypdf import PdfWriter
        except ImportError: self.skipTest('optional office parsers not installed')
        p = self.work/'short.xlsx'; workbook = Workbook(); workbook.active.append(['完整数据']); workbook.save(p); workbook.close()
        result = self.caps.execute('file_read', {'path': str(p)})
        self.assertFalse(result['extraction_limited'])
        args = {'path': str(p), 'format': 'txt'}
        self.caps.execute('document_convert', args, self.caps.preview('document_convert', args))
        long_path = self.work/'long.xlsx'; workbook = Workbook()
        for i in range(1001): workbook.active.append([i])
        workbook.save(long_path); workbook.close()
        pdf_path = self.work/'long.pdf'; pdf = PdfWriter()
        for _ in range(101): pdf.add_blank_page(width=100, height=100)
        pdf.write(str(pdf_path)); pdf.close()
        for path in (long_path, pdf_path):
            result = self.caps.execute('file_read', {'path': str(path)})
            self.assertTrue(result['extraction_limited'])
            args = {'path': str(path), 'format': 'txt'}
            with self.assertRaisesRegex(ValueError, '上限'): self.caps.execute('document_convert', args, self.caps.preview('document_convert', args))
            self.assertFalse(path.with_suffix('.txt').exists())


if __name__ == '__main__': unittest.main()
