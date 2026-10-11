import json
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from marvis.runtime import Runtime
from marvis.tui import Terminal
from llm_client import LLMClient, ChatResult


class ProgressTests(unittest.TestCase):
    def test_dialog_shows_pending_tool_and_finished_result(self):
        runtime=Mock();runtime.sessions.return_value=[{'id':'session'}]
        runtime.messages.return_value=[{'role':'user','content':'检查磁盘','task_id':'task'}]
        task={'id':'task','status':'running','created':time.time(),'events':[
            {'kind':'planning','content':{'text':'选择工具'}},
            {'kind':'tool_start','content':{'tool':'system_info','arguments':{'section':'disk'}}},
        ]}
        terminal=Terminal(runtime);output=terminal.content({'tasks':[task]})
        self.assertIn('→ 调用 system_info',output);self.assertNotIn('disk',output);self.assertIn('执行中',output)
        terminal.toggle_result(terminal.result_actions[0]);output=terminal.content({'tasks':[task]})
        self.assertIn('disk',output)
        task['events'].append({'kind':'tool_result','content':{'tool':'system_info','result':{'free_gb':500},'elapsed_ms':20}})
        task['status']='completed'
        runtime.messages.return_value.append({'role':'assistant','content':'磁盘检查完成','task_id':'task'})
        output=terminal.content({'tasks':[task]})
        self.assertIn('执行完成 · 20 ms',output);self.assertIn('free_gb',output);self.assertIn('磁盘检查完成',output)
        terminal.command('/trace');self.assertNotIn('→ 调用',terminal.content({'tasks':[task]}))

    def test_stream_is_visible_before_model_returns_and_public_note_is_saved(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=Runtime(folder); snapshots=[];rounds=[]
            def chat(messages,on_delta=None,**kwargs):
                if not rounds:
                    rounds.append(1);on_delta('先检查');on_delta('磁盘。')
                    snapshots.append(runtime.snapshot())
                    return ChatResult.success('先检查磁盘。',tool_calls=[{'id':'call','type':'function','function':{'name':'system_info','arguments':'{"section":"disk"}'}}])
                on_delta('检查完成');snapshots.append(runtime.snapshot())
                return ChatResult.success('检查完成')
            try:
                with patch.object(LLMClient,'configured',new_callable=lambda:property(lambda s:True)),patch.object(LLMClient,'chat',side_effect=chat):
                    task=runtime.submit('请帮我评估这台电脑的情况')
                    deadline=time.time()+5
                    while time.time()<deadline:
                        row=runtime.store.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
                        if row['status'] in ('completed','failed'):break
                        time.sleep(.01)
                self.assertEqual(row['status'],'completed',row['result'])
                self.assertTrue(any(e['kind']=='assistant_stream' for e in snapshots[0]['tasks'][0]['events']))
                events=runtime.store.events(task['id'])
                self.assertTrue(any(e['kind']=='assistant_note' and e['content']['text']=='先检查磁盘。' for e in events))
                self.assertTrue(any(e['kind']=='tool_result' for e in events))
                self.assertEqual(row['result'],'检查完成')
            finally:runtime.close()

    def test_sse_accumulates_split_tool_arguments_without_exposing_reasoning(self):
        chunks=[{'choices':[{'delta':{'reasoning_content':'private reasoning','content':'检查'}}]},
                {'choices':[{'delta':{'content':'电脑','tool_calls':[{'index':0,'id':'call','function':{'name':'system_info','arguments':'{"sec'}}]}}]},
                {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'arguments':'tion":"disk"}'}}]},'finish_reason':'tool_calls'}]}]
        lines=[('data: '+json.dumps(c)+'\n').encode() for c in chunks]+[b'data: [DONE]\n']
        response=Mock();response.headers={'Content-Type':'Text/Event-Stream; charset=utf-8'}
        response.__iter__=Mock(return_value=iter(lines));response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        opener=Mock();opener.open.return_value=response;parts=[];tool_parts=[]
        with patch('urllib.request.build_opener',return_value=opener):
            result=LLMClient(base_url='http://localhost/v1',model='test',api_key='local').chat([],on_delta=parts.append,on_tool_delta=lambda index,call:tool_parts.append((index,call['id'],call['function']['arguments'])))
        self.assertTrue(result.ok,result.error);self.assertEqual(parts,['检查','电脑'])
        self.assertEqual(json.loads(result.tool_calls[0]['function']['arguments']),{'section':'disk'})
        self.assertNotIn('private reasoning',result.content)
        self.assertEqual(tool_parts,[(0,'call','{"sec'),(0,'call','{"section":"disk"}')])

    def test_provider_without_streaming_is_identified(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=Runtime(folder)
            try:
                with patch.object(LLMClient,'configured',new_callable=lambda:property(lambda s:True)),patch.object(LLMClient,'chat',return_value=ChatResult.success('一次性响应')):
                    task=runtime.submit('请写一句问候')
                    deadline=time.time()+3
                    while runtime.store.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']!='completed':
                        self.assertLess(time.time(),deadline);time.sleep(.01)
                events=runtime.store.events(task['id'])
                self.assertTrue(any(e['kind']=='stream_fallback' for e in events))
                self.assertTrue(any(e['kind']=='assistant_stream' and e['content']['text']=='一次性响应' for e in events))
            finally:runtime.close()
