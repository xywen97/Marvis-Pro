import tempfile
import time
import unittest
from marvis.runtime import Runtime


class SessionManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(self.tmp.name, offline=True, config={'evaluation': {'enabled': False}})
    def tearDown(self):
        self.runtime.close(); self.tmp.cleanup()
    def test_archive_restore_preserves_tree_and_survives_restart(self):
        r = self.runtime; sid = r.new_session()
        node = r.store.add_message(sid, 'user', '保留的内容')
        before = r.session_tree(sid)
        r.session_action(sid, 'archive')
        self.assertEqual(r.sessions(), [])
        self.assertEqual(r.sessions(True)[0]['id'], sid)
        self.assertEqual(r.session_tree(sid)['nodes'], before['nodes'])
        with self.assertRaisesRegex(ValueError, '恢复'): r.submit('你好', sid)
        self.assertNotEqual(r.new_session(sid), sid)
        r.close(); self.runtime = Runtime(self.tmp.name, offline=True, config={'evaluation': {'enabled': False}}); r = self.runtime
        self.assertEqual(r.sessions(True)[0]['id'], sid)
        r.session_action(sid, 'restore')
        self.assertEqual(r.messages(sid)[0]['id'], node)
        self.assertEqual(r.sessions(True), [])
    def test_delete_cleans_all_branches_events_and_workflow_reference(self):
        r = self.runtime; sid = r.new_session(); other = r.store.session('其他会话')
        r.store.add_message(sid, 'user', '第一分支'); r.navigate_tree(sid)
        r.store.add_message(sid, 'user', '第二分支')
        now = time.time()
        r.store.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)', ('finished', sid, '任务', 'completed', '结果', now, now))
        r.store.event('finished', 'completed', {'text':'完成'})
        r.store.execute('INSERT INTO workflows VALUES (?,?,?,?,?,?,?)', ('wf','流程','你好',60,now,0,'finished'))
        r.session_action(sid, 'delete')
        for table in ('messages','tasks','events','entries'): self.assertEqual(r.store.rows('SELECT * FROM '+table), [])
        self.assertIsNone(r.store.one('SELECT last_task FROM workflows')['last_task'])
        self.assertEqual(r.sessions()[0]['id'], other)
    def test_busy_and_finalizing_tasks_are_protected(self):
        r = self.runtime; sid = r.new_session(); now = time.time()
        r.store.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)', ('busy', sid, '任务', 'queued', '', now, now))
        for action in ('archive','delete','restore'):
            with self.assertRaisesRegex(ValueError, '执行'): r.session_action(sid, action)
        r.store.execute("UPDATE tasks SET status='cancelled'")
        r.cancels['busy'] = object()
        with self.assertRaisesRegex(ValueError, '执行'): r.session_action(sid, 'delete')
        r.cancels.clear(); r.session_action(sid, 'delete')
    def test_unknown_actions_and_ids_do_not_mutate_data(self):
        sid = self.runtime.new_session()
        with self.assertRaises(ValueError): self.runtime.session_action(sid, 'unknown')
        with self.assertRaises(ValueError): self.runtime.session_action('missing', 'delete')
        self.assertEqual(self.runtime.sessions()[0]['id'], sid)

    def test_failed_delete_rolls_back_every_table(self):
        r = self.runtime; sid = r.new_session(); r.store.add_message(sid, 'user', '完整历史')
        r.store.execute("CREATE TRIGGER refuse_delete BEFORE DELETE ON sessions BEGIN SELECT RAISE(ABORT, 'fixture'); END")
        with self.assertRaises(Exception): r.session_action(sid, 'delete')
        self.assertEqual(r.messages(sid)[0]['content'], '完整历史')
        self.assertEqual(r.sessions()[0]['id'], sid)

    def test_tui_archive_restore_delete_requires_explicit_confirmation(self):
        from marvis.tui import Terminal
        r = self.runtime; terminal = Terminal(r); sid = terminal.session
        r.store.add_message(sid, 'user', '终端对话')
        terminal.command('/archive')
        self.assertNotEqual(terminal.session, sid)
        terminal.command('/archives'); self.assertIn(sid, terminal.local_text)
        terminal.command('/restore '+sid); self.assertEqual(terminal.session, sid)
        with self.assertRaisesRegex(ValueError, 'confirm'): terminal.command('/delete '+sid)
        self.assertTrue(r.messages(sid))
        terminal.command('/delete '+sid+' --confirm')
        self.assertNotEqual(terminal.session, sid); self.assertEqual(r.messages(sid), [])
