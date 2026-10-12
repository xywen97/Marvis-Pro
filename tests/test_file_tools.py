import hashlib
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from marvis.capabilities import Capabilities, validate_arguments
from marvis.file_tools import matches_glob
from marvis.runtime import Runtime


class FileToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.work = self.root / 'work'; self.work.mkdir()
        self.runtime = Runtime(self.root / 'data', offline=True)
        self.runtime.update_settings({'roots': [str(self.work)]})
        self.caps = Capabilities(self.runtime.store)

    def tearDown(self):
        self.runtime.close(); self.tmp.cleanup()

    def wait(self, task, status):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = self.runtime.store.one('SELECT * FROM tasks WHERE id=?', (task['id'],))
            if row['status'] == status: return row
            time.sleep(.01)
        self.fail(str(row))

    def edit_args(self, p, old='甲', new='乙', **extra):
        return {'path': str(p), 'old_text': old, 'new_text': new,
                'expected_sha256': self.caps.execute('file_read_lines', {'path': str(p)})['sha256'], **extra}

    def test_directory_discovery_and_pagination_exclude_private_paths(self):
        (self.work/'src').mkdir(); (self.work/'a.txt').write_text('a')
        (self.work/'.env').write_text('secret'); (self.work/'node_modules').mkdir()
        (self.work/'link').symlink_to(self.root)
        first = self.caps.execute('directory_list', {'path': str(self.work), 'limit': 1})
        second = self.caps.execute('directory_list', {'path': str(self.work), 'limit': 1, 'offset': first['next_offset']})
        self.assertEqual(first['entries'][0]['name'], 'a.txt')
        self.assertEqual(second['entries'][0]['type'], 'directory')
        self.assertEqual(second['entries'][0]['name'], 'src'); self.assertFalse(second['has_more'])

    def test_segment_globs_and_recursion(self):
        for path, pattern, expected in [('a.py', '**/*.py', True), ('src/a.py', '*.py', True),
                                        ('src/deep/a.py', 'src/*.py', False), ('src/a.py', 'src/**/*.py', True),
                                        ('src/deep/a.py', 'src/**/*.py', True), ('A.PY', '*.py', True)]:
            self.assertEqual(matches_glob(path, pattern), expected)
        (self.work/'root.py').write_text('a'); (self.work/'src').mkdir(); (self.work/'src/a.py').write_text('b')
        result = self.caps.execute('file_glob', {'path': str(self.work), 'pattern': '**/*.py', 'limit': 1})
        page = self.caps.execute('file_glob', {'path': str(self.work), 'pattern': '**/*.py', 'limit': 1, 'offset': result['next_offset']})
        self.assertEqual(len(result['matches'] + page['matches']), 2); self.assertFalse(page['has_more'])
        flat = self.caps.execute('file_glob', {'path': str(self.work), 'pattern': '*.py', 'recursive': False})
        self.assertEqual([m['relative_path'] for m in flat['matches']], ['root.py'])

    def test_search_line_context_literal_matching_and_pages(self):
        p = self.work/'a.py'; p.write_text('before\nTODO.* item\nmid\ntodo.* other\nafter\n')
        (self.work/'binary.py').write_bytes(b'\0TODO'); (self.work/'.env').write_text('TODO')
        args = {'path': str(self.work), 'query': 'TODO.*', 'glob': '*.py', 'context': 1, 'limit': 1}
        first = self.caps.execute('file_search', args)
        second = self.caps.execute('file_search', {**args, 'offset': first['next_offset']})
        self.assertEqual(first['matches'][0]['line'], 2)
        self.assertEqual([r['line'] for r in first['matches'][0]['context']], [1, 2, 3])
        self.assertEqual(second['matches'][0]['line'], 4); self.assertFalse(second['has_more'])
        strict = self.caps.execute('file_search', {**args, 'case_sensitive': True, 'limit': 100})
        self.assertEqual(len(strict['matches']), 1); self.assertEqual(strict['skipped_files'], 1)

    def test_line_read_fingerprint_and_long_lines(self):
        p = self.work/'a.txt'; raw = ('甲\r\n' + 'x'*2100 + '\r\n尾').encode(); p.write_bytes(raw)
        first = self.caps.execute('file_read_lines', {'path': str(p), 'limit': 2})
        self.assertEqual(first['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(first['newline'], 'crlf'); self.assertEqual(first['next_start_line'], 3)
        self.assertTrue(first['lines'][1]['line_truncated'])
        second = self.caps.execute('file_read_lines', {'path': str(p), 'start_line': 3})
        self.assertEqual(second['lines'][0]['text'], '尾'); self.assertFalse(second['has_more'])

    def test_edit_is_reviewed_and_preserves_crlf_and_permissions(self):
        p = self.work/'a.txt'; p.write_bytes('甲\r\n尾\r\n'.encode()); p.chmod(0o640)
        args = self.edit_args(p)
        with self.assertRaises(PermissionError): self.caps.execute('file_edit', args)
        plan = self.caps.preview('file_edit', args); self.assertIn('-甲', plan['diff']); self.assertIn('+乙', plan['diff'])
        self.assertEqual(p.read_bytes(), '甲\r\n尾\r\n'.encode())
        result = self.caps.execute('file_edit', args, plan)
        self.assertEqual(p.read_bytes(), '乙\r\n尾\r\n'.encode()); self.assertEqual(p.stat().st_mode & 0o777, 0o640)
        self.assertEqual(result['sha256'], hashlib.sha256(p.read_bytes()).hexdigest())

    def test_ambiguous_empty_and_noop_replacements(self):
        p = self.work/'a.txt'; p.write_text('甲甲')
        args = self.edit_args(p)
        with self.assertRaisesRegex(ValueError, '多处'): self.caps.preview('file_edit', args)
        args.update(replace_all=True, new_text='')
        result = self.caps.execute('file_edit', args, self.caps.preview('file_edit', args))
        self.assertEqual(result['replacements'], 2); self.assertEqual(p.read_text(), '')
        p.write_text('甲'); args = self.edit_args(p, new='甲')
        with self.assertRaisesRegex(ValueError, '相同'): self.caps.preview('file_edit', args)
        with self.assertRaises(ValueError): validate_arguments('file_edit', {**args, 'old_text': ''})
        with self.assertRaises(ValueError): validate_arguments('file_edit', {**args, 'expected_sha256': 'invalid'})

    def test_changes_after_read_or_approval_never_overwrite(self):
        p = self.work/'a.txt'; p.write_text('甲'); args = self.edit_args(p)
        plan = self.caps.preview('file_edit', args); p.write_text('外部更新')
        with self.assertRaisesRegex(ValueError, '已变化'): self.caps.execute('file_edit', args, plan)
        with self.assertRaisesRegex(ValueError, '已变化'): self.caps.preview('file_edit', args)
        self.assertEqual(p.read_text(), '外部更新')

    def test_diff_without_final_newline_and_whitespace_edit(self):
        p = self.work/'a.txt'; p.write_text('甲  尾')
        args = self.edit_args(p, old='  ', new=' '); plan = self.caps.preview('file_edit', args)
        self.assertIn('-甲  尾\n', plan['diff']); self.assertIn('+甲 尾\n', plan['diff'])
        self.caps.execute('file_edit', args, plan); self.assertEqual(p.read_text(), '甲 尾')

    def test_search_stops_at_total_content_budget(self):
        for i in range(11): (self.work/f'{i:02}.txt').write_text('a' * (2 * 1024 * 1024))
        result = self.caps.execute('file_search', {'path': str(self.work), 'query': 'absent'})
        self.assertTrue(result['scan_truncated'])
        self.assertEqual(result['bytes_scanned'], result['content_scan_limit_bytes'])
        self.assertFalse(result['matches'])

    def test_replace_failure_keeps_original_and_cleans_temporary_file(self):
        p = self.work/'a.txt'; p.write_text('甲'); args = self.edit_args(p); plan = self.caps.preview('file_edit', args)
        with patch('marvis.file_tools.os.replace', side_effect=OSError('failure')), self.assertRaises(OSError):
            self.caps.execute('file_edit', args, plan)
        self.assertEqual(p.read_text(), '甲'); self.assertEqual(list(self.work.iterdir()), [p])

    def test_new_tools_keep_authorization_and_protected_file_boundaries(self):
        outside = self.root/'outside.txt'; outside.write_text('甲')
        for name, args in [('directory_list', {'path': str(self.root)}), ('file_glob', {'path': str(self.root), 'pattern': '*'}),
                           ('file_search', {'path': str(outside), 'query': '甲'}), ('file_read_lines', {'path': str(outside)})]:
            with self.subTest(name=name), self.assertRaises(PermissionError): self.caps.execute(name, args)
        p = self.work/'a.txt'; p.write_text('甲'); link = self.work/'link.txt'; link.symlink_to(p)
        with self.assertRaisesRegex(ValueError, '符号链接'): self.caps.preview('file_edit', {**self.edit_args(p), 'path': str(link)})

    def test_runtime_edit_rejection_and_changed_approval(self):
        p = self.work/'a.txt'; p.write_text('甲')
        task = self.runtime.submit('编辑', action={'tool': 'file_edit', 'arguments': self.edit_args(p)})
        self.wait(task, 'waiting_approval'); self.runtime.approve(task['id'], False)
        self.wait(task, 'completed'); self.assertEqual(p.read_text(), '甲')
        task = self.runtime.submit('编辑', action={'tool': 'file_edit', 'arguments': self.edit_args(p)})
        self.wait(task, 'waiting_approval'); p.write_text('外部更新'); self.runtime.approve(task['id'], True)
        self.wait(task, 'failed'); self.assertEqual(p.read_text(), '外部更新')
        task = self.runtime.submit('编辑', action={'tool': 'file_edit', 'arguments': self.edit_args(p, old='外部更新', new='确认更新')})
        self.wait(task, 'waiting_approval'); self.runtime.approve(task['id'], True)
        self.wait(task, 'completed'); self.assertEqual(p.read_text(), '确认更新')


if __name__ == '__main__': unittest.main()
