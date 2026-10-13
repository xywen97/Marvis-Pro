import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from marvis.capabilities import Capabilities
from marvis.execution_evidence import unresolved_failures
from marvis.runtime import Runtime
from marvis.task_authorization import TaskAuthorization
from marvis.task_timing import task_timing
from marvis.team import tool_message
from marvis.prompts import build_worker_prompt, build_system_prompt


class ExecutionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.runtime = Runtime(self.root / 'data', offline=True, config={'evaluation': {'enabled': False}})
        self.runtime.update_settings({'roots': [str(self.root)]})
        self.caps = Capabilities(self.runtime.store)

    def tearDown(self):
        self.runtime.close()
        self.tmp.cleanup()

    def wait(self, task, statuses):
        until = time.monotonic() + 5
        while time.monotonic() < until:
            row = self.runtime.store.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
            if row['status'] in statuses: return row
            time.sleep(.01)
        self.fail(str(row))

    def script(self, name='example.py', text="print('passed')\n"):
        path = self.root / name
        path.write_text(text)
        return path

    def command(self, path, **extra):
        return {'argv': ['python3', str(path)], 'cwd': str(self.root), **extra}

    def lease(self, path):
        args = {'path': str(path), 'text': "print('passed')\n"}
        path.unlink(missing_ok=True)
        plan = self.caps.preview('file_write', args)
        offer = TaskAuthorization.offer(self.caps, 'file_write', plan, args)
        self.caps.execute('file_write', args, plan)
        authorization = TaskAuthorization()
        authorization.grant(self.caps, offer, time.monotonic())
        return authorization

    def test_clean_python_environment_and_prefer_project_interpreter(self):
        path = self.script(text="import os; assert not any(k in os.environ for k in ('PYTHONHOME','PYTHONPATH','VIRTUAL_ENV','__PYVENV_LAUNCHER__')); print('clean')\n")
        bindir = self.root / '.venv' / 'bin'; bindir.mkdir(parents=True)
        (bindir / 'python3').symlink_to(sys.executable)
        with patch.dict(os.environ, {'PYTHONHOME': '/missing/runtime', 'PYTHONPATH': '/missing/packages', 'VIRTUAL_ENV': '/wrong/env', '__PYVENV_LAUNCHER__': '/wrong/python'}):
            args = self.command(path)
            plan = self.caps.preview('code_execute', args)
            self.assertEqual(plan['argv'][0], str(bindir / 'python3'))
            self.assertEqual(plan['verification']['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            result = self.caps.execute('code_execute', args, plan)
        self.assertEqual(result['exit_code'], 0, result)
        self.assertIn('clean', result['output'])

    def test_validation_lease_is_pinned_to_artifact_interpreter_directory_and_deadline(self):
        path = self.root / 'example.py'; auth = self.lease(path)
        args = self.command(path); plan = self.caps.preview('code_execute', args)
        self.assertTrue(auth.allows('code_execute', plan))
        doctest = {**args, 'argv': ['python3', '-m', 'doctest', str(path)]}
        self.assertTrue(auth.allows('code_execute', self.caps.preview('code_execute', doctest)))
        other = self.script('other.py')
        (self.root / 'other-work').mkdir()
        for candidate in (self.command(other), {**args, 'timeout': 61},
                          {**args, 'argv': ['python3', '-c', 'print(1)']},
                          {**args, 'argv': ['python3', '-m', 'unittest']},
                          {**args, 'argv': ['python3', str(path), 'extra']},
                          {**args, 'cwd': str(self.root / 'other-work')}):
            self.assertFalse(auth.allows('code_execute', self.caps.preview('code_execute', candidate)))
        self.assertFalse(auth.allows('file_edit', {'path': str(path)}))
        path.write_text("print('changed')\n")
        self.assertFalse(auth.allows('code_execute', self.caps.preview('code_execute', args)))
        with self.assertRaises(PermissionError): self.caps.execute('code_execute', args, plan)
        path.write_text("print('passed')\n")
        auth.verifications[0]['expires_at'] = 0
        self.assertFalse(auth.allows('code_execute', plan))

    def test_changed_interpreter_or_file_cannot_be_granted_after_preview(self):
        path = self.root / 'example.py'
        args = {'path': str(path), 'text': "print('passed')\n"}
        plan = self.caps.preview('file_write', args)
        offer = TaskAuthorization.offer(self.caps, 'file_write', plan, args)
        path.write_text("print('external edit')\n")
        auth = TaskAuthorization(); auth.grant(self.caps, offer, time.monotonic())
        self.assertFalse(auth.verifications)
        path.write_text(args['text'])
        offer['interpreter_identity'] = ['wrong', 0, 0, 0, 0]
        auth.grant(self.caps, offer, time.monotonic())
        self.assertFalse(auth.verifications)

    def test_one_explicit_approval_covers_write_and_real_verification_only(self):
        path = self.root / 'example.py'
        def chat(ident, session, prompt, caps, settings):
            self.runtime._tool(ident, caps, 'file_write', {'path': str(path), 'text': "assert sorted([3,1,2]) == [1,2,3]\nprint('passed')\n"})
            result = self.runtime._tool(ident, caps, 'code_execute', self.command(path))
            self.assertEqual(result['exit_code'], 0)
            return 'verified'
        with patch.object(self.runtime, '_chat', side_effect=chat):
            task = self.runtime.submit('保存并验证一个 Python 文件')
            self.wait(task, {'waiting_approval'})
            with self.assertRaisesRegex(ValueError, '已变化'):
                self.runtime.approve(task['id'], True, task_scope=True, approval_id='stale-preview')
            self.runtime.approve(task['id'], True, task_scope=True)
            row = self.wait(task, {'completed', 'failed'})
        self.assertEqual(row['status'], 'completed', row['result'])
        events = self.runtime.store.events(task['id'])
        self.assertEqual(sum(e['kind'] == 'approval' for e in events), 1)
        self.assertTrue(any(e['kind'] == 'approved' and e['content'].get('source') == 'task_scope' for e in events))
        # A fresh task never inherits the previous task's authorization.
        new = self.runtime.submit('再次运行', action={'tool': 'code_execute', 'arguments': self.command(path)})
        self.wait(new, {'waiting_approval'}); self.runtime.approve(new['id'], False)
        self.assertEqual(self.wait(new, {'completed', 'failed'})['status'], 'completed')
        self.assertTrue(any(e['kind'] == 'rejected' for e in self.runtime.store.events(new['id'])))

    def test_single_operation_approval_does_not_create_validation_lease(self):
        path = self.root / 'example.py'
        def chat(ident, session, prompt, caps, settings):
            self.runtime._tool(ident, caps, 'file_write', {'path': str(path), 'text': "print('passed')\n"})
            self.runtime._tool(ident, caps, 'code_execute', self.command(path))
            return 'done'
        with patch.object(self.runtime, '_chat', side_effect=chat):
            task = self.runtime.submit('写入并验证')
            self.wait(task, {'waiting_approval'}); self.runtime.approve(task['id'], True)
            until = time.monotonic() + 5
            while sum(e['kind'] == 'approval' for e in self.runtime.store.events(task['id'])) < 2:
                self.assertLess(time.monotonic(), until); time.sleep(.01)
            with self.assertRaises(ValueError): self.runtime.approve(task['id'], True, task_scope=True)
            self.runtime.approve(task['id'], False)
            self.assertEqual(self.wait(task, {'completed', 'failed'})['status'], 'completed')

    def test_generated_search_exclusion_can_be_overridden_without_changing_read_access(self):
        self.script('source.py')
        for directory in ('build', 'dist', 'vendor', 'target'):
            (self.root / directory).mkdir(); (self.root / directory / 'dependency.py').write_text('needle')
        args = {'path': str(self.root), 'pattern': '*.py'}
        result = self.caps.execute('file_glob', args)
        self.assertEqual([r['relative_path'] for r in result['matches']], ['source.py'])
        self.assertEqual(len(self.caps.execute('file_glob', {**args, 'include_generated': True})['matches']), 5)
        direct = self.caps.execute('file_glob', {**args, 'path': str(self.root / 'build')})
        self.assertEqual(len(direct['matches']), 1)
        search = {'path': str(self.root), 'query': 'needle'}
        self.assertFalse(self.caps.execute('file_search', search)['matches'])
        self.assertEqual(len(self.caps.execute('file_search', {**search, 'include_generated': True})['matches']), 4)

    def test_terminal_task_timestamp_survives_background_memory_event(self):
        task = self.runtime.submit('hello'); self.wait(task, {'completed'})
        before = self.runtime.store.one('SELECT updated FROM tasks WHERE id=?', (task['id'],))['updated']
        time.sleep(.02)
        self.runtime.store.event(task['id'], 'memory_saved', {'text': 'saved'})
        after = self.runtime.store.one('SELECT updated FROM tasks WHERE id=?', (task['id'],))['updated']
        self.assertEqual(before, after)
        snapshot = next(t for t in self.runtime.snapshot()['tasks'] if t['id'] == task['id'])
        self.assertGreater(snapshot['timing']['background_ms'], 0)

    def test_compact_worker_evidence_keeps_outcomes_and_complete_audit_input(self):
        report = {'version': 1, 'agent': 'coding', 'status': 'completed', 'summary': 'saved and verified', 'unresolved_failures': [], 'evidence': [
            {'tool': 'file_read_lines', 'call_id': 'read', 'result': {'path': '/a.py', 'lines': ['source'] * 200, 'sha256': 'hash', 'has_more': False}},
            {'tool': 'file_write', 'call_id': 'write', 'arguments': {'path': '/a.py', 'text': 'code' * 1000}, 'result': {'created': '/a.py', 'bytes': 4000}},
            {'tool': 'code_execute', 'call_id': 'run', 'result': {'exit_code': 0, 'output': 'passed', 'verification': {'sha256': 'hash'}}}]}
        message = json.loads(tool_message(report))
        self.assertEqual(message['evidence'][0]['result']['sha256'], 'hash')
        self.assertEqual(message['evidence'][0]['result']['lines_count'], 200)
        self.assertEqual(message['evidence'][2]['result']['output'], 'passed')
        self.assertIn('lines', report['evidence'][0]['result'])
        self.assertEqual(len(report['evidence'][1]['arguments']['text']), 4000)


class RecoveryAndTimingTests(unittest.TestCase):
    def item(self, call, error=False, path='/a.py', digest='hash', check='script'):
        result = {'cwd': '/work', 'verification': {'path': path, 'sha256': digest, 'check': check}, 'exit_code': 1 if error else 0}
        if error: result['error'] = 'startup failed'
        return {'tool': 'code_execute', 'call_id': call, 'arguments': {}, 'result': result}

    def test_only_successful_equivalent_check_resolves_failure(self):
        bad = self.item('bad', True)
        self.assertEqual(unresolved_failures([bad, self.item('good')]), [])
        for later in (self.item('other', path='/b.py'), self.item('changed', digest='new'), self.item('different', check='doctest')):
            self.assertEqual(unresolved_failures([bad, later]), ['bad'])
        rejected = {**bad, 'result': {'rejected': True}}
        self.assertEqual(unresolved_failures([rejected, self.item('good')]), ['bad'])
        self.assertEqual(unresolved_failures([self.item('good'), bad]), ['bad'])

    def test_false_syntax_result_is_failure_and_successful_recheck_recovers_it(self):
        bad = {'tool': 'code_check', 'call_id': 'bad', 'result': {'path': '/a.py', 'valid': False}}
        good = {'tool': 'code_check', 'call_id': 'good', 'result': {'path': '/a.py', 'valid': True}}
        self.assertEqual(unresolved_failures([bad]), ['bad'])
        self.assertEqual(unresolved_failures([bad, good]), [])

    def test_timing_excludes_background_and_separates_wait_model_and_tools(self):
        def event(kind, stamp, **data): return {'kind': kind, 'created': stamp, 'content': data}
        task = {'created': 100, 'updated': 180, 'status': 'completed', 'events': [
            event('model_start', 100, agent='main'), event('model_end', 105, agent='main'),
            event('approval', 105, call_id='write'), event('approved', 125, call_id='write'),
            event('tool_result', 125.1, elapsed_ms=100), event('completed', 126), event('memory_saved', 180)]}
        result = task_timing(task, now=200)
        self.assertEqual(result, {'finished_at': 126, 'total_ms': 26000, 'approval_ms': 20000, 'model_ms': 5000, 'tool_ms': 100, 'background_ms': 54000})
        task['events'] = [event('model_start', 100, agent='main'), event('context_usage', 103, agent='main'), event('completed', 104)]
        self.assertEqual(task_timing(task)['model_ms'], 3000)
        task['status'] = 'waiting_approval'; task['events'] = [event('approval', 105, call_id='write')]
        self.assertEqual(task_timing(task, now=110)['approval_ms'], 5000)
        task['status'] = 'completed'; task['events'] = [event('model_start', 100, agent='main', step=1),
            event('context_usage', 102, agent='main'), event('model_end', 105, agent='main', step=1), event('completed', 106)]
        self.assertEqual(task_timing(task)['model_ms'], 5000)


if __name__ == '__main__': unittest.main()
