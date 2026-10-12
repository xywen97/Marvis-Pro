import tempfile
import time
import unittest
from marvis.runtime import Runtime
from marvis.app_commands import catalog, execute


class AppCommandsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = Runtime(self.directory.name, offline=True, config={'evaluation': {'enabled': False}})
        self.sid = self.runtime.new_session()

    def tearDown(self):
        self.runtime.close(); self.directory.cleanup()

    def turn(self, text):
        task = self.runtime.submit(text,self.sid)
        until = time.monotonic()+3
        while task['id'] in self.runtime.cancels and time.monotonic()<until: time.sleep(.01)
        self.assertNotIn(task['id'],self.runtime.cancels)
        return self.runtime.messages(self.sid)[-1]

    def test_commands_are_not_model_prompts_and_aliases_match_tui(self):
        self.assertEqual(execute(self.runtime,'/t',self.sid)['view'],'tree')
        self.assertIn('/tree (t)',execute(self.runtime,'/h',self.sid)['text'])
        self.assertEqual(self.runtime.snapshot()['tasks'],[])
        self.assertEqual(next(c for c in catalog() if c['name']=='/quit')['aliases'],['/q'])
        with self.assertRaisesRegex(ValueError,'未知命令'): execute(self.runtime,'/unknown',self.sid)
        with self.assertRaisesRegex(ValueError,'confirm'): execute(self.runtime,'/del '+self.sid,self.sid)

    def test_evaluation_aliases_open_intake_without_model_task(self):
        for command in ('/evaluate', '/eval', '/评测'):
            self.assertEqual(execute(self.runtime, command, self.sid)['view'], 'evaluation')
        self.assertEqual(self.runtime.snapshot()['tasks'], [])
        entry = next(c for c in catalog() if c['name'] == '/evaluate')
        self.assertIn('/eval', entry['aliases']); self.assertIn('/评测', entry['aliases'])

    def test_fork_copies_only_ancestry_and_leaves_source_unchanged(self):
        first = self.turn('起点'); self.turn('不要带入新分支的后续消息')
        before = self.runtime.session_tree(self.sid)
        sid = self.runtime.fork_session(self.sid,first['id'])['session_id']
        self.assertNotEqual(sid,self.sid)
        self.assertEqual(self.runtime.session_tree(self.sid),before)
        self.assertEqual([m['content'] for m in self.runtime.messages(sid)],
                         [m['content'] for m in self.runtime.messages(self.sid)[:2]])
        self.assertNotIn('不要带入',str(self.runtime.context_messages(sid)))
        user = self.runtime.messages(self.sid)[0]
        user_sid = self.runtime.fork_session(self.sid,user['id'])['session_id']
        self.assertEqual(len(self.runtime.messages(user_sid)),1)
        self.assertEqual(len(self.runtime.sessions()),3)
        # A copied chat can be independently deleted without changing the source.
        self.runtime.session_action(sid,'delete')
        self.assertEqual(self.runtime.session_tree(self.sid),before)

    def test_tool_fork_preserves_partial_context_and_remaps_events(self):
        self.turn('工具历史')
        task=self.runtime.snapshot()['tasks'][0]['id']
        # Add a deterministic public note/call/result chain without executing tools.
        self.runtime.store.event(task,'assistant_note',{'text':'公开说明'})
        self.runtime.store.event(task,'tool_start',{'tool':'system_info','arguments':{},'call_id':'example'})
        self.runtime.store.event(task,'tool_result',{'tool':'system_info','result':{'demo':True},'call_id':'example'})
        nodes=self.runtime.session_tree(self.sid)['nodes']
        selected=next(n for n in nodes if n['kind']=='tool_call')
        sid=self.runtime.fork_session(self.sid,selected['id'])['session_id']
        branch=self.runtime.session_tree(sid)
        self.assertEqual(branch['nodes'][-1]['kind'],'tool_call')
        self.assertNotIn('demo',str(self.runtime.context_messages(sid)))
        self.assertIn('No result provided',str(self.runtime.context_messages(sid)))
        for n in branch['nodes']:
            if n['event_id']: self.assertEqual(n['id'],'event-'+str(n['event_id']))
        # The copied public bubble can itself be branched again.
        thought=next(n for n in branch['nodes'] if n['kind']=='thinking')
        self.runtime.fork_session(sid,thought['id'])
