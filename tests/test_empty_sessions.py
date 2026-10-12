import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from marvis.runtime import Runtime


class EmptySessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.runtime=Runtime(self.tmp.name,offline=True, config={'evaluation': {'enabled': False}})
    def tearDown(self):self.runtime.close();self.tmp.cleanup()
    def test_repeated_and_concurrent_new_requests_reuse_one_empty_session(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids=list(pool.map(lambda _:self.runtime.new_session(),range(20)))
        self.assertEqual(len(set(ids)),1);self.assertEqual(len(self.runtime.sessions()),1)
        self.assertEqual(self.runtime.new_session(ids[0]),ids[0])
    def test_populated_session_creates_one_new_empty_session(self):
        first=self.runtime.new_session();self.runtime.store.add_message(first,'user','有内容')
        second=self.runtime.new_session(first)
        self.assertNotEqual(first,second)
        self.assertEqual(self.runtime.new_session(first),second)
        self.assertEqual(self.runtime.new_session(second),second)
        self.assertEqual(len(self.runtime.sessions()),2)
    def test_current_legacy_empty_is_preferred_without_deleting_other_history(self):
        first=self.runtime.store.session();second=self.runtime.store.session()
        self.assertEqual(self.runtime.new_session(first),first)
        self.assertEqual(len(self.runtime.sessions()),2)
        self.assertTrue(all(s['is_empty'] for s in self.runtime.sessions()))
        self.assertEqual(self.runtime.new_session(),second)
    def test_tree_root_with_preserved_history_is_not_an_empty_session(self):
        first=self.runtime.new_session();self.runtime.store.add_message(first,'user','旧分支')
        self.runtime.navigate_tree(first)
        self.assertEqual(self.runtime.messages(first),[])
        self.assertFalse(self.runtime.sessions()[0]['is_empty'])
        self.assertNotEqual(self.runtime.new_session(first),first)
