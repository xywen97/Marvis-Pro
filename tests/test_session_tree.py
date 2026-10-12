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
from marvis.capabilities import Capabilities


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
        nav.query='重要结论';self.assertEqual(len(nav.rows()),2)
        nav.query='找不到';self.assertEqual(len(nav.rows()),1)
        nav.query='';nav.filter=2;self.assertEqual(len(nav.rows()),2)
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

    def tool_turn(self):
        self.runtime.offline=False
        def chat(messages, **kwargs):
            if messages[-1]['role']=='user':
                return ChatResult.success('公开步骤说明：先检查，再整理。',tool_calls=[
                    {'id':'first-call','type':'function','function':{'name':'agent_inspect','arguments':'{}'}},
                    {'id':'second-call','type':'function','function':{'name':'agent_inspect','arguments':'{}'}}])
            return ChatResult.success('未来最终回答，不应出现在较早节点的分支中。')
        markers=iter(['disk-result','future-memory-result'])
        with patch.object(LLMClient,'configured',new_callable=lambda:property(lambda s:True)),patch.object(LLMClient,'chat',side_effect=chat),patch.object(Capabilities,'execute',side_effect=lambda name,args,*rest:{'marker':next(markers)}):
            self.turn('执行一次完整评估')
        return self.runtime.session_tree(self.sid)['nodes']

    def test_step_navigation_rebuilds_exact_branch_context_and_preserves_session(self):
        nodes=self.tool_turn(); original_ids={n['id'] for n in nodes}
        self.assertEqual([n['kind'] for n in nodes],['user','thinking','tool_call','tool_result','tool_call','tool_result','assistant'])
        terminal=Terminal(self.runtime); terminal.command('/tree')
        self.assertEqual(len(terminal.navigator.rows()),8)
        for selected in nodes[1:4]:
            with self.subTest(kind=selected['kind']):
                terminal.input='旧草稿';terminal.select_tree(selected['id'])
                self.assertEqual(terminal.input,'');self.assertEqual(terminal.session,self.sid)
                self.assertEqual(self.runtime.store.entry_branch(self.sid)[-1]['id'],selected['id'])
                captured=[]
                def chat(messages,**kwargs):captured.extend(messages);return ChatResult.success('分支答案')
                with patch.object(LLMClient,'configured',new_callable=lambda:property(lambda s:True)),patch.object(LLMClient,'chat',side_effect=chat):
                    self.turn('从当前步骤继续')
                text=str(captured)
                self.assertIn('公开步骤说明',text)
                self.assertNotIn('未来最终回答',text);self.assertNotIn('future-memory-result',text)
                if selected['kind']=='tool_result':
                    self.assertIn('disk-result',text)
                    self.assertEqual(captured[-2]['role'],'tool')
                elif selected['kind']=='tool_call':
                    self.assertIn('No result provided',text);self.assertNotIn('disk-result',text)
                else:self.assertNotIn('tool_calls',text)
        new_nodes=self.runtime.session_tree(self.sid)['nodes']
        self.assertTrue(original_ids.issubset({n['id'] for n in new_nodes}))
        self.assertEqual(len(self.runtime.sessions()),1)
        self.runtime.close();self.runtime=Runtime(self.tmp.name,offline=True)
        self.assertEqual(self.runtime.session_tree(self.sid)['nodes'],new_nodes)

    def test_user_edit_after_tool_result_branches_before_original_user(self):
        nodes=self.tool_turn();answer=nodes[-1];self.runtime.offline=True
        self.turn('下一轮原始问题'); user=self.runtime.messages(self.sid)[-2]
        terminal=Terminal(self.runtime);terminal.select_tree(user['id'])
        self.assertEqual(terminal.input,'下一轮原始问题')
        self.assertEqual(self.runtime.store.entry_branch(self.sid)[-1]['id'],answer['id'])
        self.turn('下一轮改写问题')
        new_user=self.runtime.messages(self.sid)[-2]
        entry=self.runtime.store.one('SELECT * FROM entries WHERE id=?',(new_user['id'],))
        self.assertEqual(entry['parent_id'],answer['id'])
        self.assertNotIn('下一轮原始问题',str(self.runtime.context_messages(self.sid)))

    def test_arrow_navigation_can_enter_every_step_and_filters_hide_intermediate_entries(self):
        import curses
        nodes=self.tool_turn();terminal=Terminal(self.runtime);terminal.command('/t')
        terminal.tree_key(curses.KEY_UP)
        self.assertEqual(terminal.navigator.current()['kind'],'tool_result')
        terminal.tree_key('\n');self.assertEqual(terminal.input,'')
        self.assertEqual(self.runtime.store.entry_branch(self.sid)[-1]['kind'],'tool_result')
        terminal.command('/tree');terminal.tree_key(curses.KEY_UP)
        self.assertEqual(terminal.navigator.current()['kind'],'tool_call')
        terminal.tree_key(curses.KEY_UP);self.assertEqual(terminal.navigator.current()['kind'],'tool_result')
        terminal.tree_key(curses.KEY_UP);terminal.tree_key(curses.KEY_UP)
        self.assertEqual(terminal.navigator.current()['kind'],'thinking')
        terminal.tree_key('\n');self.assertEqual(terminal.input,'')
        terminal.command('/tree');terminal.tree_key(curses.KEY_UP);terminal.tree_key('\n')
        self.assertEqual(terminal.input,'执行一次完整评估')
        self.assertEqual(self.runtime.store.entry_branch(self.sid),[])
        nav=Navigator(self.runtime.session_tree(self.sid));nav.filter=3
        self.assertFalse(any(n.get('kind') in ('tool_call','tool_result') for n in nav.rows()))
        nav.filter=0;nav.query='disk missing-keyword'
        self.assertEqual(len(nav.rows()),1)  # Search requires every token.
        nav.query='disk';self.assertTrue(all('disk' in n['content'] for n in nav.rows()[1:]))

    def test_optional_summary_is_a_new_branch_entry_without_reparenting_old_branch(self):
        first=self.turn('共同起点');old=self.turn('离开的分支问题')
        original=self.runtime.session_tree(self.sid)['nodes'];self.runtime.offline=False
        with patch.object(LLMClient,'configured',new_callable=lambda:property(lambda s:True)),patch.object(LLMClient,'chat',return_value=ChatResult.success('离开的分支摘要')):
            result=self.runtime.navigate_tree(self.sid,first['id'],summarize=True)
        self.assertIsNotNone(result['summary_id'])
        self.assertEqual(self.runtime.store.entry_branch(self.sid)[-1]['kind'],'branch_summary')
        self.assertIn('离开的分支摘要',str(self.runtime.context_messages(self.sid)))
        current={n['id']:n for n in self.runtime.session_tree(self.sid)['nodes']}
        for n in original:self.assertEqual(current[n['id']]['parent_id'],n['parent_id'])
        self.runtime.navigate_tree(self.sid,old['id'])
        self.assertEqual(self.runtime.messages(self.sid)[-1]['id'],old['id'])

    def test_legacy_step_migration_keeps_tool_history_and_selected_final_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            db=sqlite3.connect(str(Path(directory)/'marvis.sqlite3'))
            db.executescript('''CREATE TABLE sessions(id TEXT PRIMARY KEY,title TEXT,created REAL,active_leaf TEXT);
                CREATE TABLE messages(id TEXT PRIMARY KEY,session_id TEXT,role TEXT,content TEXT,task_id TEXT,created REAL,parent_id TEXT,label TEXT);
                CREATE TABLE tasks(id TEXT PRIMARY KEY,session_id TEXT,prompt TEXT,status TEXT,result TEXT,created REAL,updated REAL);
                CREATE TABLE events(id INTEGER PRIMARY KEY,task_id TEXT,kind TEXT,content TEXT,created REAL);
                INSERT INTO sessions VALUES('s','旧会话',1,'answer');
                INSERT INTO messages VALUES('user','s','user','问题','task',1,NULL,'');
                INSERT INTO messages VALUES('answer','s','assistant','回答','task',3,'user','书签');
                INSERT INTO tasks VALUES('task','s','问题','completed','回答',1,3);
                INSERT INTO events VALUES(1,'task','assistant_note','{"text":"公开行动说明"}',1.1);
                INSERT INTO events VALUES(2,'task','tool_start','{"tool":"system_info","call_id":"call","arguments":{}}',2);
                INSERT INTO events VALUES(3,'task','tool_result','{"tool":"system_info","call_id":"call","result":{"disk":42}}',2.1);''')
            db.commit();db.close();store=Store(directory)
            try:
                path=store.entry_branch('s')
                self.assertEqual([n['kind'] for n in path],['user','thinking','tool_call','tool_result','assistant'])
                self.assertEqual(path[-1]['label'],'书签')
                self.assertEqual(store.one('SELECT content FROM messages WHERE id="answer"')['content'],'回答')
            finally:store.close()
