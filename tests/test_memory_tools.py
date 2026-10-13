import json
import threading
import time
import unittest
from unittest.mock import patch
from llm_client import LLMClient, ChatResult
from marvis.memory import MemoryEngine
from marvis.memory_skill import memory_intent
import test_memory as memory_tests


class MemoryToolTests(unittest.TestCase):
    setUp = memory_tests.MemoryTests.setUp
    tearDown = memory_tests.MemoryTests.tearDown
    enqueue = memory_tests.MemoryTests.enqueue
    def wait_task(self, task):
        self.runtime.pool.submit(lambda:None).result(timeout=5)
        row=self.runtime.store.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
        self.assertEqual(row['status'],'completed',row['result'])
        return row

    def test_simple_save_then_new_session_search_no_file_tools_or_model_planning(self):
        self.runtime.memory=MemoryEngine(self.runtime)
        actual_save=self.runtime.memory.save
        self.memory_records=[{'uri':'viking://user/marvisxy/memories/profile.md','abstract':'姓名：温翔宇；喜欢榴莲和芒果。','level':2}]
        with patch.object(self.runtime.memory,'save',side_effect=lambda *a,**kw:actual_save(*a[:4],wait_seconds=0,check=a[5])),patch.object(LLMClient,'chat',side_effect=AssertionError('No model planning needed')):
            first=self.runtime.submit('请记住我的长期偏好：我的名字是温翔宇，我比较喜欢吃榴莲和芒果',self.sid)
            first_row=self.wait_task(first)
            self.assertIn('正在处理中',first_row['result'])
            self.assertNotIn('记住了',first_row['result'])
            self.assertEqual(self.runtime.memory.status()['deliveries'].get('pending',0)+self.runtime.memory.status()['deliveries'].get('extracting',0),1)
            timer=threading.Timer(.2,lambda:setattr(self,'remote_status','completed'));timer.start()
            second=self.runtime.submit('我是谁',self.runtime.new_session())
            second_row=self.wait_task(second);timer.join()
            self.assertIn('温翔宇',second_row['result']);self.assertIn('榴莲',second_row['result'])
        for task, expected in [(first,'memory_save'),(second,'memory_search')]:
            starts=[e['content']['tool'] for e in self.runtime.store.events(task['id']) if e['kind']=='tool_start']
            self.assertEqual(starts,['skill_load',expected])
        self.assertEqual(len(self.runtime.store.rows('SELECT * FROM memory_outbox')),1)
        self.assertEqual(len([call for call in self.calls if call[1].endswith('/messages/batch')]),1)
        self.assertEqual(len([call for call in self.calls if call[1].endswith('/commit')]),1)
        snapshot=self.runtime.snapshot()
        saved=next(t for t in snapshot['tasks'] if t['id']==first['id'])['memory_requests'][0]
        self.assertEqual(saved['status'],'saved')

    def test_explicit_save_dedup_and_actual_write_confirmation(self):
        m=self.runtime.memory
        first=m.save(self.ident,self.sid,'姓名：温翔宇',self.runtime.settings(),0)
        second=m.save(self.ident,self.sid,'姓名：温翔宇',self.runtime.settings(),0)
        self.assertEqual(first['request_id'],second['request_id']);self.assertEqual(first['status'],'processing')
        self.enqueue();self.assertEqual(len(self.runtime.store.rows('SELECT * FROM memory_outbox')),1)
        self.remote_status='completed';m.flush_once()
        row=self.runtime.store.one('SELECT * FROM memory_outbox')
        self.assertEqual(m.request_status(row)['status'],'saved')
        self.assertEqual(self.messages[row['remote_session']][0]['content'],'姓名：温翔宇')
        self.runtime.close();self.runtime=__import__('marvis.runtime',fromlist=['Runtime']).Runtime(self.tmp.name,config=self.config);self.runtime.memory.close()
        again=self.runtime.memory.save(self.ident,self.sid,'姓名：温翔宇',self.runtime.settings(),0)
        self.assertEqual(again['status'],'saved');self.assertEqual(again['request_id'],first['request_id'])
        self.assertEqual(len([call for call in self.calls if call[1].endswith('/commit')]),1)

    def test_zero_writes_and_failed_saves_never_certify_saved(self):
        m=self.runtime.memory;m.save(self.ident,self.sid,'test',self.runtime.settings(),0)
        self.memory_write_count=0;self.remote_status='completed';m.flush_once()
        self.assertEqual(m.request_status(self.runtime.store.one('SELECT * FROM memory_outbox'))['status'],'no_change')
        m.save(self.ident,self.sid,'other',self.runtime.settings(),0)
        self.remote_status='failed';m.flush_once()
        self.assertEqual(m.request_status(self.runtime.store.one("SELECT * FROM memory_outbox WHERE status='failed'"))['status'],'failed')

    def test_directory_overview_is_not_a_user_fact_and_pending_is_not_absence(self):
        self.memory_records=[{'uri':'viking://user/marvisxy/memories/.overview.md','abstract':'Memory storage structure','level':1}]
        m=self.runtime.memory
        result=m.search(self.ident,'我是谁',self.runtime.settings(),0)
        self.assertEqual(result['status'],'not_found');self.assertEqual(result['records'],[])
        m.save(self.ident,self.sid,'姓名：温翔宇',self.runtime.settings(),0)
        result=m.search(self.ident,'我是谁',self.runtime.settings(),0)
        self.assertEqual(result['status'],'processing');self.assertEqual(len(result['pending']),1)
        self.assertEqual(result['records'],[])
        self.fail_search=True
        self.assertEqual(m.search(self.ident,'我是谁',self.runtime.settings(),0)['status'],'unavailable')

    def test_persona_templates_are_excluded_and_food_reply_is_relevant(self):
        from marvis.memory_skill import memory_reply
        self.memory_records=[
            {'uri':'viking://user/marvisxy/memories/preferences/food.md','abstract':'比较喜欢吃榴莲和芒果。','level':2},
            {'uri':'viking://user/marvisxy/memories/soul.md','abstract':'Core Truths: Be helpful','level':2},
            {'uri':'viking://user/marvisxy/memories/profile.md','abstract':'姓名：温翔宇','level':2},
            {'uri':'viking://user/marvisxy/memories/identity.md','abstract':'Name: MarvisXY','level':2}]
        result=self.runtime.memory.search(self.ident,'我喜欢吃什么',self.runtime.settings(),0)
        self.assertEqual(len(result['records']),2)
        self.assertEqual(memory_reply(result,'search','我喜欢吃什么'),'比较喜欢吃榴莲和芒果。')
        seen=[]
        def summarize(messages,**kwargs):
            seen.append(messages)
            self.assertEqual(kwargs['tools'],[]);self.assertEqual(kwargs['tool_choice'],'none')
            self.assertNotIn('Core Truths',json.dumps(messages));self.assertNotIn('温翔宇',json.dumps(messages,ensure_ascii=False))
            return ChatResult.success('你喜欢吃榴莲和芒果。')
        with patch.object(LLMClient,'configured',new_callable=lambda:property(lambda s:True)),patch.object(LLMClient,'chat',side_effect=summarize):
            task=self.runtime.submit('我喜欢吃什么',self.sid)
            row=self.wait_task(task)
        self.assertEqual(row['result'],'你喜欢吃榴莲和芒果。');self.assertEqual(len(seen),1)
        self.assertEqual([e['content']['tool'] for e in self.runtime.store.events(task['id']) if e['kind']=='tool_start'],['skill_load','memory_search'])

    def test_disabled_save_search_make_no_network_requests(self):
        self.runtime.update_settings({'memory_enabled':False})
        before=len(self.calls)
        self.assertEqual(self.runtime.memory.save(self.ident,self.sid,'text',self.runtime.settings(),0)['status'],'disabled')
        self.assertEqual(self.runtime.memory.search(self.ident,'query',self.runtime.settings(),0)['status'],'disabled')
        self.assertEqual(before,len(self.calls));self.assertEqual(self.runtime.store.rows('SELECT * FROM memory_outbox'),[])

    def test_general_model_cannot_claim_saved_after_processing_tool_result(self):
        replies=[ChatResult.success('',tool_calls=[{'id':'load','function':{'name':'skill_load','arguments':'{"name":"long-term-memory"}'}}]),
                 ChatResult.success('',tool_calls=[{'id':'save','function':{'name':'memory_save','arguments':'{"text":"用户喜欢榴莲","wait_seconds":0}'}}]),
                 ChatResult.success('已经记住了，你喜欢榴莲。')]
        with patch.object(LLMClient,'configured',new_callable=lambda:property(lambda s:True)),patch.object(LLMClient,'chat',side_effect=replies):
            task=self.runtime.submit('请保存这项信息并做简短说明：我喜欢榴莲',self.sid)
            row=self.wait_task(task)
        self.assertNotIn('记住了',row['result']);self.assertIn('尚未确认保存完成',row['result'])
        self.assertTrue(any(e['kind']=='memory_reply_corrected' for e in self.runtime.store.events(task['id'])))
        self.assertFalse(any(e['kind'] in ('assistant_stream','assistant_note') and '记住了' in e['content'].get('text','')
                             for e in self.runtime.store.events(task['id'])))
        self.assertEqual(len(self.runtime.store.rows('SELECT * FROM memory_outbox')),1)

    def test_fast_path_does_not_capture_mixed_actions(self):
        self.assertEqual(memory_intent('请记住我的名字是温翔宇'),'save')
        self.assertEqual(memory_intent('我的名字是什么？'),'search')
        self.assertIsNone(memory_intent('请记住我喜欢榴莲，然后帮我整理文件'))

    def test_cancel_stops_waiting_but_retains_explicitly_submitted_save(self):
        self.runtime.memory=MemoryEngine(self.runtime)
        task=self.runtime.submit('请记住我的名字是温翔宇',self.sid)
        end=time.monotonic()+2
        while time.monotonic()<end and not self.runtime.store.one('SELECT * FROM memory_outbox'): time.sleep(.01)
        self.assertIsNotNone(self.runtime.store.one('SELECT * FROM memory_outbox'))
        self.runtime.cancel(task['id'])
        self.runtime.pool.submit(lambda:None).result(timeout=2)
        row=self.runtime.store.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
        self.assertEqual(row['status'],'cancelled')
        self.assertEqual(len(self.runtime.store.rows('SELECT * FROM memory_outbox')),1)
