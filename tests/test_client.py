import tempfile
import unittest
import time
from marvis.runtime import Runtime
from marvis.client import connect
from marvis.server import serve


class ClientTests(unittest.TestCase):
    def test_remote_tree_navigation_and_bookmark(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Runtime(directory,offline=True, config={'evaluation': {'enabled': False}});server,_=serve(runtime)
            try:
                client=connect(directory);sid=client.new_session()
                task=client.submit('你好',sid)
                deadline=time.time()+3
                while runtime.store.one('SELECT status FROM tasks WHERE id=?',(task['id'],))['status']!='completed':
                    self.assertLess(time.time(),deadline);time.sleep(.01)
                tree=client.session_tree(sid);self.assertEqual(len(tree['nodes']),2)
                user=next(n for n in tree['nodes'] if n['role']=='user')
                client.label_node(sid,user['id'],'起点')
                self.assertEqual(client.navigate_tree(sid,user['id'])['input'],'你好')
                self.assertEqual(client.messages(sid),[])
                self.assertEqual(len(client.session_tree(sid)['nodes']),2)
                client.close()
            finally:server.shutdown();server.server_close();runtime.close()

    def test_second_client_shares_engine_and_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime=Runtime(directory,offline=True, config={'evaluation': {'enabled': False}});server,_=serve(runtime)
            try:
                client=connect(directory);self.assertIsNotNone(client)
                self.assertEqual(client.settings()['offline'],True)
                sid=client.new_session();task=client.submit('检查磁盘空间',sid)
                self.assertEqual(task['session_id'],sid)
                self.assertTrue(any(s['id']==sid for s in runtime.sessions()))
                with self.assertRaises(RuntimeError):Runtime(directory,offline=True, config={'evaluation': {'enabled': False}})
                client.close()
                self.assertEqual(runtime.closed.is_set(),False)
            finally:server.shutdown();server.server_close();runtime.close()
