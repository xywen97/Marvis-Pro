import curses
import unittest
from unittest.mock import Mock
from marvis.transcript import StreamAnimator, dialogue_rows, wrapped
from marvis.tui import Terminal


class TUIStreamingTests(unittest.TestCase):
    def sample(self):
        return {'id':'t','created':0,'status':'running','events':[
            {'id':1,'kind':'model_start','content':{'step':1,'model':'test'}},
            {'id':2,'kind':'assistant_stream','content':{'step':1,'text':'先检查磁盘和内存，随后整理结果。'}},
            {'id':3,'kind':'tool_call_stream','content':{'step':1,'call_id':'a','tool':'system_info','arguments':'{"section":'}},
            {'id':4,'kind':'tool_start','content':{'step':1,'call_id':'a','tool':'system_info','arguments':{'section':'disk'}}},
            {'id':5,'kind':'tool_result','content':{'step':1,'call_id':'a','tool':'system_info','elapsed_ms':10,'result':{'disk_result':1}}},
            {'id':6,'kind':'tool_start','content':{'step':1,'call_id':'b','tool':'system_info','arguments':{'section':'memory'}}},
            {'id':7,'kind':'tool_result','content':{'step':1,'call_id':'b','tool':'system_info','elapsed_ms':20,'result':{'memory_result':2}}},
        ]}
    def test_animation_reveals_public_text_and_flushes_finished_notes(self):
        animator=StreamAnimator();task=self.sample();snapshot={'tasks':[task]}
        first=animator.apply(snapshot,1)['tasks'][0]['events'][1]['content']['text']
        second=animator.apply(snapshot,1.03)['tasks'][0]['events'][1]['content']['text']
        self.assertTrue(second.startswith(first));self.assertLess(len(first),len(second))
        self.assertLess(len(second),len(task['events'][1]['content']['text']))
        self.assertEqual(snapshot['tasks'][0]['events'][1]['content']['text'],'先检查磁盘和内存，随后整理结果。')
        task['events'][1]['kind']='assistant_note'
        finished=animator.apply(snapshot,1.06)['tasks'][0]['events'][1]['content']['text']
        self.assertEqual(finished,task['events'][1]['content']['text'])
    def test_repeated_tools_have_separate_fold_state_and_keep_step(self):
        task=self.sample();messages=[{'role':'user','content':'检查电脑','task_id':'t'}]
        rows=dialogue_rows(messages,[task]);text='\n'.join(row.text for row in rows)
        self.assertEqual(text.count('→ 调用 system_info'),2)
        self.assertIn('Step 1',text);self.assertNotIn('disk_result',text);self.assertNotIn('memory_result',text)
        rows=dialogue_rows(messages,[task],expanded={'t:a'});text='\n'.join(row.text for row in rows)
        self.assertIn('disk_result',text);self.assertNotIn('memory_result',text)
        self.assertEqual({row.action for row in wrapped(rows,40) if row.action},{'t:a','t:b'})
    def test_key_controls_keep_editor_input_and_fold_selection(self):
        runtime=Mock();runtime.sessions.return_value=[{'id':'s'}]
        runtime.messages.return_value=[{'role':'user','content':'检查电脑','task_id':'t'}]
        runtime.snapshot.return_value={'tasks':[self.sample()]}
        terminal=Terminal(runtime);terminal.input='尚未提交的问题';terminal.dialogue(runtime.snapshot())
        terminal.result_focus='t:a';terminal.result_key(curses.KEY_RIGHT)
        self.assertIn('t:a',terminal.expanded_results)
        terminal.result_key(curses.KEY_DOWN);self.assertEqual(terminal.result_focus,'t:b')
        terminal.result_key('\n');self.assertIn('t:b',terminal.expanded_results)
        terminal.result_key(curses.KEY_LEFT);self.assertNotIn('t:b',terminal.expanded_results)
        terminal.result_key('\x1b');self.assertIsNone(terminal.result_focus)
        self.assertEqual(terminal.input,'尚未提交的问题')
        terminal.command('/result 2');self.assertIn('t:b',terminal.expanded_results)
