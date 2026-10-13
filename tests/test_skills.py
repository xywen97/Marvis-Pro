import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from llm_client import ChatResult, LLMClient
from marvis.runtime import Runtime
from marvis.skill_catalog import SkillCatalog
from marvis.capabilities import Capabilities
from marvis.coordinator import Coordinator


class SkillsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self): self.tmp.cleanup()

    def skill(self, root=None, name='answer-style', body='BODY_ONLY_WHEN_LOADED', meta=''):
        path = (root or self.root) / name
        path.mkdir(parents=True, exist_ok=True)
        (path / 'SKILL.md').write_text('---\nname: '+name+'\ndescription: Format an answer when requested.\n'+meta+'---\n'+body)
        return path

    def test_catalog_metadata_first_and_optional_fields(self):
        self.skill(meta='license: MIT\ncompatibility: Python 3.12\nallowed-tools: file_write\nmetadata:\n  author: marvis\n')
        c = SkillCatalog([self.root])
        self.assertNotIn('BODY_ONLY_WHEN_LOADED', c.prompt())
        self.assertIn('answer-style', c.prompt())
        loaded = c.load('answer-style')
        self.assertEqual(loaded['instructions'], 'BODY_ONLY_WHEN_LOADED')
        self.assertEqual(loaded['metadata']['metadata'], {'author': 'marvis'})
        self.assertEqual(c.list()['errors'], [])

    def test_invalid_skills_do_not_hide_valid_skill(self):
        self.skill()
        cases = {'mismatch': '---\nname: other\ndescription: test\n---\nbody',
                 'duplicate': '---\nname: duplicate\nname: duplicate\ndescription: test\n---\nbody',
                 'invalid': '---\nname: invalid\ndescription: [test]\n---\nbody',
                 'empty': '---\nname: empty\ndescription: test\n---\n',
                 'large': '---\nname: large\ndescription: test\n---\n'+'x'*16001,
                 'python': '---\nname: python\ndescription: !!python/object/apply:os.system [echo unsafe]\n---\nbody'}
        for name, text in cases.items():
            p=self.root/name;p.mkdir();(p/'SKILL.md').write_text(text)
        c = SkillCatalog([self.root])
        self.assertEqual([s['name'] for s in c.list()['skills']], ['answer-style'])
        self.assertEqual(len(c.list()['errors']), len(cases))

    def test_overrides_refresh_and_revalidation(self):
        self.skill(self.root/'first', body='first')
        p=self.skill(self.root/'last', body='last')
        c=SkillCatalog([self.root/'first', self.root/'last'])
        self.assertEqual(c.load('answer-style')['instructions'], 'last')
        self.assertEqual(len(c.list()['errors']), 1)
        (p/'SKILL.md').write_text('invalid')
        with self.assertRaises(ValueError): c.load('answer-style')
        c.refresh()
        self.assertEqual(c.load('answer-style')['instructions'], 'first')

    def test_relative_resources_pagination_and_escape_boundaries(self):
        p=self.skill();(p/'references').mkdir();(p/'references/note.md').write_text('甲乙丙丁')
        (p/'scripts').mkdir();(p/'scripts/test.py').write_text("raise RuntimeError('must not execute')")
        outside=self.root/'private';outside.write_text('private')
        (p/'references/link.md').symlink_to(outside)
        (p/'assets').mkdir();(p/'assets/binary').write_bytes(b'\x00\x01')
        (p/'references/large.md').write_bytes(b'x'*262145)
        c=SkillCatalog([self.root])
        page=c.read('answer-style','references/note.md',limit=2)
        self.assertEqual(page['text'],'甲乙');self.assertTrue(page['has_more'])
        self.assertEqual(c.read('answer-style','references/note.md',offset=page['next_offset'])['text'],'丙丁')
        self.assertIn('must not execute',c.read('answer-style','scripts/test.py')['text'])
        for resource in ['../private', str(outside), 'references/link.md', 'assets/binary', 'references/large.md', '.env']:
            with self.subTest(resource=resource),self.assertRaises(ValueError): c.read('answer-style',resource)
        (self.root/'linked').symlink_to(p,target_is_directory=True)
        c.refresh();self.assertTrue(c.list()['errors'])

    def test_online_model_loads_skill_and_resource_through_audited_tools(self):
        # Install after startup: the next task discovers it without an engine restart.
        runtime=Runtime(self.root/'data', config={'skills':{'paths':[str(self.root/'installed')]}})
        try:
            p=self.skill(self.root/'installed');(p/'references').mkdir();(p/'references/note.md').write_text('RESOURCE_CONTENT')
            observed=[]
            def respond(messages, **kwargs):
                observed.append(messages)
                results=[json.loads(m['content']) for m in messages if m['role']=='tool']
                if not results:
                    self.assertIn('answer-style', messages[0]['content'])
                    self.assertNotIn('BODY_ONLY_WHEN_LOADED',messages[0]['content'])
                    name,args='skill_load',{'name':'answer-style'}
                elif len(results)==1:
                    self.assertEqual(results[0]['instructions'],'BODY_ONLY_WHEN_LOADED')
                    name,args='skill_read',{'name':'answer-style','resource':'references/note.md'}
                else:
                    self.assertEqual(results[1]['text'],'RESOURCE_CONTENT')
                    return ChatResult.success('已按技能完成')
                return ChatResult.success('',tool_calls=[{'id':str(len(results)),'function':{'name':name,'arguments':json.dumps(args)}}])
            with patch.object(LLMClient,'configured',new_callable=lambda:property(lambda s:True)),patch.object(LLMClient,'chat',side_effect=respond):
                task=runtime.submit('使用 answer-style 技能回答')
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    row=runtime.store.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
                    if row['status'] in ('completed','failed'):break
                    time.sleep(.01)
            self.assertEqual(row['status'],'completed',row['result'])
            events=runtime.store.events(task['id'])
            self.assertEqual([e['content']['tool'] for e in events if e['kind']=='tool_result'],['skill_load','skill_read'])
            self.assertEqual(len(observed),3)
            caps=Capabilities(runtime.store);caps.skill_catalog=runtime.skills
            with self.assertRaises(ValueError):caps.execute('skill_read',{'name':'answer-style','resource':'references/note.md'})
            coordinator=Coordinator(runtime,task['id'],task['session_id'],caps,runtime.settings())
            with self.assertRaises(PermissionError):coordinator.physical_tool('file',{'function':{'name':'skill_load','arguments':'{"name":"answer-style"}'}})
            self.assertEqual(runtime.snapshot()['skills']['skills'][0]['name'],'answer-style')
        finally:runtime.close()

    def test_direct_offline_skill_tools_work_without_model(self):
        runtime=Runtime(self.root/'data', offline=True)
        try:
            self.skill(runtime.store.directory/'skills')
            task=runtime.submit('/tool skill_list {}')
            end=time.monotonic()+5
            while time.monotonic()<end:
                row=runtime.store.one('SELECT * FROM tasks WHERE id=?',(task['id'],))
                if row['status'] in ('completed','failed'):break
                time.sleep(.01)
            self.assertEqual(row['status'],'completed',row['result'])
            self.assertIn('answer-style',row['result'])
        finally:runtime.close()


if __name__=='__main__':unittest.main()
