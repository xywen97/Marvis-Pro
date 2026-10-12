import sys
import time
import unittest
from unittest.mock import patch

import tempfile
from pathlib import Path
from marvis.runtime import Runtime
from marvis.capabilities import Capabilities, validate_arguments


class CodingToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name).resolve()
        self.work = root / 'work'; self.work.mkdir()
        self.runtime = Runtime(root / 'data', offline=True, config={'evaluation': {'enabled': False}})
        self.runtime.update_settings({'roots': [str(self.work)]})
        self.caps = Capabilities(self.runtime.store)

    def tearDown(self):
        self.runtime.close(); self.tmp.cleanup()

    def test_syntax_without_execution(self):
        p = self.work / 'example.py'
        p.write_text("raise RuntimeError('must not execute')\n")
        self.assertTrue(self.caps.execute('code_check', {'path': str(p)})['valid'])
        p.write_text('def broken(\n')
        result = self.caps.execute('code_check', {'path': str(p)})
        self.assertFalse(result['valid']); self.assertEqual(result['line'], 1)
        p = self.work / 'example.json'; p.write_text('{oops}')
        self.assertFalse(self.caps.execute('code_check', {'path': str(p)})['valid'])

    def args(self, script, **extra):
        return {'argv': [sys.executable, '-c', script], 'cwd': str(self.work), **extra}

    def test_approval_literal_args_exit_and_output_bound(self):
        args = self.args("import sys; print(sys.argv[1])")
        args['argv'].append('$(touch unexpected)')
        with self.assertRaises(PermissionError): self.caps.execute('code_execute', args)
        plan = self.caps.preview('code_execute', args)
        result = self.caps.execute('code_execute', args, plan)
        self.assertEqual(result['exit_code'], 0)
        self.assertIn('$(touch unexpected)', result['output'])
        self.assertFalse((self.work / 'unexpected').exists())
        args = self.args("print('x'*100000); raise SystemExit(3)")
        result = self.caps.execute('code_execute', args, self.caps.preview('code_execute', args))
        self.assertEqual(result['exit_code'], 3); self.assertIn('error', result)
        self.assertTrue(result['truncated']); self.assertLessEqual(len(result['output']), 20000)

    def test_timeout_cancel_and_changed_plan(self):
        args = self.args('import time; time.sleep(10)', timeout=1)
        plan = self.caps.preview('code_execute', args)
        self.assertTrue(self.caps.execute('code_execute', args, plan)['timed_out'])
        with self.assertRaises(PermissionError):
            self.caps.execute('code_execute', {**args, 'timeout': 2}, plan)
        real = __import__('subprocess').Popen; children = []
        def capture(*a, **kw):
            child = real(*a, **kw); children.append(child); return child
        count = 0
        def cancel():
            nonlocal count
            count += 1
            if count >= 4: raise RuntimeError('cancelled')
        self.caps.check = cancel
        with patch('marvis.coding_tools.subprocess.Popen', side_effect=capture):
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                self.caps.execute('code_execute', args, plan)
        self.assertIsNotNone(children[0].poll())

    def test_local_mode_and_argument_validation(self):
        args = self.args('print(1)')
        caps = Capabilities(self.runtime.store, local=True)
        with self.assertRaises(PermissionError): caps.preview('code_execute', args)
        with self.assertRaises(PermissionError): caps.execute('code_execute', args, {'fake': True})
        self.assertIn('code_execute', caps.inspect_agents()['unavailable_tools'])
        for argv in [[], 'python', [1], [''], ['python', '\x00']]:
            with self.assertRaises(ValueError): validate_arguments('code_execute', {**args, 'argv': argv})

    def test_runtime_waits_for_approval_before_running_command(self):
        args = self.args("from pathlib import Path; Path('verified.txt').write_text('passed')")
        task = self.runtime.submit('执行代码验证', action={'tool': 'code_execute', 'arguments': args})
        def wait_for(statuses):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                row = self.runtime.store.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
                if row['status'] in statuses: return row
                time.sleep(.01)
            self.fail('任务状态等待超时')
        wait_for({'waiting_approval'})
        self.assertFalse((self.work / 'verified.txt').exists())
        self.runtime.approve(task['id'], True)
        self.assertEqual(wait_for({'completed', 'failed'})['status'], 'completed')
        self.assertEqual((self.work / 'verified.txt').read_text(), 'passed')
