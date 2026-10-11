import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from marvis.runtime import Runtime
from marvis.session_tree import Navigator
from marvis.store import Store
from marvis.tui import Terminal
from llm_client import LLMClient, ChatResult


class SessionTreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.runtime = Runtime(self.tmp.name, offline=True)
        self.sid = self.runtime.new_session()
    def tearDown(self):self.runtime.close();self.tmp.cleanup()
    def turn(self, text):
        task = self.runtime.submit(text, self.sid)
        deadline = time.time() + 3
        while time.time() < deadline:
            if self.runtime.store.one('SELECT status FROM tasks WHERE id=?', (task['id'],))['status'] == 'completed':
                return self.runtime.messages(self.sid)[-1]
            time.sleep(.01)
        self.fail('task timed out')

    def test_branch_switch_preserves_old_nodes_and_model_context(self):
        first = self.turn('共同起点');old = self.turn('旧分支问题')
        self.runtime.navigate_tree(self.sid, first['id'])
        self.runtime.offline = False;seen=[]
        def chat(messages, **kwargs):
            seen.extend(m['content'] for m in messages if m['role']=='user');return ChatResult.success('新分支回答')
        with patch.object(LLMClient,'configured',new_callable=lambda:property(lambda s:True)),patch.object(LLMClient,'chat',side_effect=chat):
            new = self.turn('新分支问题')
        self.assertEqual(seen,['共同起点','新分支问题'])
        all_nodes=self.runtime.session_tree(self.sid)['nodes']
        self.assertEqual(len(all_nodes),6)
        self.assertIn(old['id'],{n['id'] for n in all_nodes})
        self.runtime.navigate_tree(self.sid,old['id'])
        self.assertEqual(self.runtime.messages(self.sid)[-2]['content'],'旧分支问题')
        self.runtime.navigate_tree(self.sid,new['id'])
        self.assertEqual(self.runtime.messages(self.sid)[-1]['content'],'新分支回答')

    def test_user_selection_prefills_editor_and_root_can_fork(self):
        self.turn('原始问题');user=self.runtime.messages(self.sid)[0]
        terminal=Terminal(self.runtime);terminal.select_tree(user['id'])
        self.assertEqual(terminal.input,'原始问题')
        self.assertEqual(self.runtime.messages(self.sid),[])
        self.turn('修改后的问题')
        nodes=self.runtime.session_tree(self.sid)['nodes']
        self.assertEqual(len([n for n in nodes if n['parent_id'] is None]),2)

    def test_label_navigation_filter_and_fold(self):
        self.turn('唯一关键词');answer=self.runtime.messages(self.sid)[-1]
        self.runtime.label_node(self.sid,answer['id'],'重要结论')
        nav=Navigator(self.runtime.session_tree(self.sid))
        nav.query='重要结论';self.assertEqual(len(nav.rows()),3)
        nav.query='找不到';self.assertEqual(len(nav.rows()),1)
        nav.query='';nav.filter=2;self.assertEqual(len(nav.rows()),3)
        nav.filter=0;nav.selected=None;nav.fold();self.assertEqual(len(nav.rows()),1)
        nav.unfold();self.assertEqual(len(nav.rows()),3)

    def test_active_task_blocks_branch_switch_and_foreign_node_rejected(self):
        self.runtime.update_settings({'roots':[self.tmp.name]})
        task=self.runtime.submit('写文件',self.sid,{'tool':'file_write','arguments':{'path':str(Path(self.tmp.name).parent/'outside.txt'),'text':'x'}})
        # Avoid timing assumptions: reserve a queued status explicitly after task finishes.
        deadline=time.time()+3
        while self.runtime.store.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status'] not in ('failed','completed') and time.time()<deadline:time.sleep(.01)
        self.runtime.store.execute("UPDATE tasks SET status='queued' WHERE id=?",(task['id'],))
        with self.assertRaises(ValueError):self.runtime.navigate_tree(self.sid)
        self.runtime.store.execute("UPDATE tasks SET status='failed' WHERE id=?",(task['id'],))
        other=self.runtime.new_session();foreign=self.runtime.store.add_message(other,'user','other')
        with self.assertRaises(ValueError):self.runtime.navigate_tree(self.sid,foreign)

    def test_branch_and_labels_survive_restart(self):
        first=self.turn('第一轮');self.turn('第二轮')
        self.runtime.navigate_tree(self.sid,first['id']);self.runtime.label_node(self.sid,first['id'],'分叉处')
        self.runtime.close();self.runtime=Runtime(self.tmp.name,offline=True)
        self.assertEqual(len(self.runtime.messages(self.sid)),2)
        self.assertEqual(len(self.runtime.session_tree(self.sid)['nodes']),4)
        self.assertEqual(self.runtime.messages(self.sid)[-1]['label'],'分叉处')

    def test_legacy_database_migration_keeps_linear_history(self):
        with tempfile.TemporaryDirectory() as directory:
            db=sqlite3.connect(str(Path(directory)/'marvis.sqlite3'))
            db.executescript('CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT,created REAL); CREATE TABLE messages(id TEXT PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,task_id TEXT,created REAL); INSERT INTO sessions VALUES("s","旧会话",1); INSERT INTO messages VALUES("u","s","user","你好",NULL,1); INSERT INTO messages VALUES("a","s","assistant","欢迎",NULL,2);')
            db.commit();db.close();store=Store(directory)
            try:
                leaf=store.one('SELECT active_leaf FROM sessions WHERE id="s"')['active_leaf']
                self.assertEqual([n['id'] for n in store.branch('s',leaf)],['u','a'])
                self.assertEqual(store.one('SELECT content FROM messages WHERE id="u"')['content'],'你好')
            finally:store.close()
