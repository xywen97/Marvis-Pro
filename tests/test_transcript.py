import unittest
from unittest.mock import Mock, patch
import curses
from marvis.transcript import dialogue_rows, wrapped, columns
from marvis.tui import Terminal


class TranscriptTests(unittest.TestCase):
    def sample(self):
        messages=[{'role':'user','content':'检查电脑','task_id':'t'},{'role':'assistant','content':'检查完成。','task_id':'t'}]
        task={'id':'t','status':'completed','events':[
            {'kind':'assistant_note','content':{'text':'先查询系统信息。'}},
            {'kind':'tool_start','content':{'tool':'system_info','arguments':{'section':'overview'}}},
            {'kind':'tool_result','content':{'tool':'system_info','elapsed_ms':20,'result':{'output':'物理内存\n总计：24 GB'}}},
        ]}
        return dialogue_rows(messages,[task],expanded={'t:1'})

    def test_tree_styles_and_nested_tool_results(self):
        rows=self.sample();text='\n'.join(r.text for r in rows)
        self.assertIn('├─ 执行过程',text)
        self.assertIn('│     └─ [-] → 调用 system_info',text)
        self.assertIn('└─ ✦ MarvisXY',text)
        self.assertNotIn('\\n总计',text)
        self.assertEqual(next(r.tone for r in rows if '先查询' in r.text),'thought')
        self.assertEqual(next(r.tone for r in rows if '→ 调用' in r.text),'muted')
        self.assertEqual(next(r.tone for r in rows if '执行完成' in r.text),'success')
        self.assertEqual(rows[0].tone,'user')

    def test_wrapping_keeps_tree_guides_and_color(self):
        rows=self.sample()
        thought=next(r for r in rows if r.tone=='thought')
        thought.text+='中文说明'*30
        result=wrapped([thought],36)
        self.assertGreater(len(result),1)
        self.assertTrue(all(columns(r.text)<=36 for r in result))
        self.assertTrue(all(r.tone=='thought' for r in result))
        self.assertTrue(all(r.text.startswith(thought.continuation) for r in result[1:]))

    def test_thought_is_dimmed_and_tool_is_bold(self):
        with patch('curses.color_pair',side_effect=lambda n:n<<16):
            self.assertTrue(Terminal.line_attr('thought') & curses.A_DIM)
            self.assertTrue(Terminal.line_attr('tool') & curses.A_BOLD)
            self.assertNotEqual(Terminal.line_attr('tool'),Terminal.line_attr('success'))

    def test_trace_toggle_retains_answer(self):
        runtime=Mock();runtime.sessions.return_value=[{'id':'s'}]
        runtime.messages.return_value=[{'role':'user','content':'你好','task_id':'t'},{'role':'assistant','content':'你好，老板','task_id':'t'}]
        terminal=Terminal(runtime);terminal.show_trace=False
        text=terminal.content({'tasks':[{'id':'t','status':'completed','events':[]}]})
        self.assertIn('你好，老板',text);self.assertNotIn('执行过程',text)
